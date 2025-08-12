# esm3c.py — ESM-3c with multi-layer export + FP32 casting

from esm.models.esmc import ESMC
from esm.sdk.api import ESMProtein, LogitsConfig
import torch


def load_model(model_name, conf):
    device = torch.device(conf["embedding"].get("device", "cuda"))
    model = ESMC.from_pretrained(model_name).to(device)
    # 🔧 Force FP32 to avoid BF16 on devices/ops that don't support it
    model = model.to(torch.float32)  # (esto estaba en tu versión previa)
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
                    hs = [t.to(torch.float32) for t in hs]  # FP32 list
                if emb_seq is not None:
                    emb_seq = emb_seq.to(torch.float32)

                layer_tensors = {}
                if hs is not None:
                    for li in set(layer_index_list):
                        layer_tensors[li] = hs[-(li + 1)]  # [1, L, D] FP32
                else:
                    layer_tensors[0] = emb_seq  # FP32
                    if any(li != 0 for li in layer_index_list):
                        layer_index_list = [0]

                for li in layer_index_list:
                    layer_tensor = layer_tensors[li]           # [1, L, D] FP32
                    emb = layer_tensor[0, 1:-1].mean(dim=0)    # [D] FP32
                    record = {
                        "sequence_id": sequence_id,
                        "embedding_type_id": embedding_type_id,
                        "layer_index": li,
                        "sequence": sequence,
                        "embedding": emb.cpu().numpy().tolist(),  # NumPy OK en FP32
                        "shape": emb.shape,
                    }
                    print(record)

                    embedding_records.append(record)
            except Exception as e:
                # Robustness: continue processing remaining batches; free GPU cache on failure.
                print(f"Error processing batch {i // batch_size}: {e}")
                torch.cuda.empty_cache()
                continue

        return embedding_records