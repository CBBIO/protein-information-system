# esm3c.py — ESM-3c with multi-layer export + FP32 casting

from esm.models.esmc import ESMC
from esm.sdk.api import ESMProtein, LogitsConfig
import torch

from protein_information_system.helpers.layers import validate_layer_indices


def load_model(model_name, conf):
    device = torch.device(conf["embedding"].get("device", "cuda"))
    model = ESMC.from_pretrained(model_name).to(device)
    model = model.to(torch.float32)  #
    model.eval()
    return model


def load_tokenizer(model_name=None):
    return None


def embedding_task(
        sequences,
        model,
        tokenizer,
        device,
        batch_size="NOT_SUPPORTED",
        embedding_type_id=None,
        layer_index_list=None,
):
    if layer_index_list is None:
        layer_index_list = [0]

    model.to(device)
    embedding_records = []

    with torch.no_grad():
        for seq_info in sequences:
            sequence = seq_info["sequence"]
            sequence_id = seq_info.get("sequence_id")
            try:
                protein = ESMProtein(sequence=sequence)
                protein_tensor = model.encode(protein)

                logits_output = model.logits(
                    protein_tensor,
                    LogitsConfig(
                        sequence=True,
                        return_embeddings=True,
                        return_hidden_states=True,
                    ),
                )

                # 🔧 Ensure FP32 for all tensors we will use
                hs = logits_output.hidden_states
                emb_seq = logits_output.embeddings

                if hs is not None:
                    # ESM3c SDK returns hidden_states as a tensor with shape [N_layers, 1, L, D]
                    if isinstance(hs, torch.Tensor):
                        # Normalize to list of [1, L, D] tensors for consistency
                        hs = [hs[i].to(torch.float32) for i in range(hs.shape[0])]
                    else:
                        hs = [t.to(torch.float32) for t in hs]
                if emb_seq is not None:
                    emb_seq = emb_seq.to(torch.float32)

                # ✅ Validate configured layer indices
                valid_layers = validate_layer_indices(
                    layer_index_list,
                    hs,  # may be None
                    model_tag="ESM3c",
                    sequence_id=sequence_id,
                )

                layer_tensors = {}
                if hs is None:
                    # embeddings-only (valid_layers must be [0])
                    layer_tensors[0] = emb_seq
                else:
                    for li in valid_layers:
                        layer_tensors[li] = hs[-(li + 1)]  # [1, L, D] FP32

                for li in valid_layers:
                    layer_tensor = layer_tensors[li]  # [1, L, D]
                    emb = layer_tensor[0, 1:-1].mean(dim=0)  # [D] FP32

                    record = {
                        "sequence_id": sequence_id,
                        "embedding_type_id": embedding_type_id,
                        "layer_index": li,
                        "sequence": sequence,
                        "embedding": emb.cpu().numpy().tolist(),
                        "shape": emb.shape,
                    }
                    embedding_records.append(record)

            except Exception as e:
                # Robustness: continue processing remaining batches; free GPU cache on failure.
                print(f"❌ Failed to process sequence {sequence_id}: {e}")
                torch.cuda.empty_cache()
                continue

        return embedding_records
