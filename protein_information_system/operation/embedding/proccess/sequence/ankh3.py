# ankh3.py — Backend for computing embeddings with Ankh3 (T5-encoder family)
from transformers import T5Tokenizer, T5EncoderModel
import torch

from protein_information_system.helpers.layers import validate_layer_indices


def load_model(model_name, conf):
    """
    Load a T5 encoder configured to return all hidden states.

    Args:
        model_name (str): Hugging Face model identifier or local path.
        conf (dict): Global configuration; must include 'embedding' → 'device'.

    Returns:
        T5EncoderModel: Model moved to the target device, eval mode, appropriate dtype.
    """
    device = torch.device(conf['embedding'].get('device', "cuda"))
    dtype = torch.float16 if device.type == "cuda" else torch.float32

    # output_hidden_states=True → enables selecting arbitrary layers after a single forward
    model = T5EncoderModel.from_pretrained(
        model_name,
        output_hidden_states=True,
        torch_dtype=dtype
    ).to(device).eval()
    return model


def load_tokenizer(model_name):
    """
    Load the tokenizer associated with the (Ankh) T5 encoder model.
    """
    return T5Tokenizer.from_pretrained(model_name)


def embedding_task(
        sequences,
        model,
        tokenizer,
        device,
        batch_size=8,
        embedding_type_id=None,
        layer_index_list=None,
):
    """
    Compute per-sequence embeddings for one or more hidden-state layers in a single forward pass.

    Layer indexing convention:
        layer_index = 0 → last layer
        layer_index = 1 → penultimate layer
        layer_index = 2 → antepenultimate layer
        ...

    Args:
        sequences (list[dict]): Items with at least:
            - 'sequence_id': unique identifier
            - 'sequence'   : raw amino-acid string
        model (T5EncoderModel): Preloaded encoder with output_hidden_states=True.
        tokenizer (T5Tokenizer): Preloaded tokenizer.
        device (str | torch.device): 'cuda' or 'cpu'.
        batch_size (int): Number of sequences per batch.
        embedding_type_id (int | None): Embedding type identifier propagated downstream.
        layer_index_list (list[int] | None): Layers to export; defaults to [0].

    Returns:
        list[dict]: One record per sequence per requested layer with:
            - sequence_id
            - embedding_type_id
            - layer_index
            - sequence
            - embedding (list[float])
            - shape (torch.Size)
    """
    if layer_index_list is None:
        layer_index_list = [0]  # default: export last layer

    embedding_records = []

    # Process sequences in batches
    for i in range(0, len(sequences), batch_size):
        batch = sequences[i:i + batch_size]

        # Ankh convention: prepend a control token "[NLU]" to the raw sequence.
        # We keep add_special_tokens=True to let the tokenizer handle BOS/EOS as needed.
        protein_sequences = ["[NLU]" + seq["sequence"] for seq in batch]

        inputs = tokenizer.batch_encode_plus(
            protein_sequences,
            add_special_tokens=True,
            padding="longest",
            truncation=False,
            return_tensors="pt",
        ).to(device)

        with torch.no_grad():
            try:
                # Single forward for the whole batch; reuse hidden_states for all requested layers
                outputs = model(**inputs)
                hidden_states = outputs.hidden_states  # tuple: [emb, layer1, ..., last]; shapes [B, L, D]

                # Validate configured layer indices once per batch (reverse indexing: 0=last)
                valid_layers = validate_layer_indices(
                    layer_index_list,
                    hidden_states,
                    model_tag="Ankh3",
                    sequence_id=f"batch-{i // batch_size}",
                )

                # Broadcastable mask to exclude padding from the mean (1=valid token, 0=padding)
                mask = inputs.attention_mask.unsqueeze(-1).type_as(hidden_states[-1])  # [B, L, 1]

                for li in valid_layers:
                    # Reverse selection: 0 -> last, 1 -> penultimate, ...
                    layer_tensor = hidden_states[-(li + 1)]  # [B, L, D]

                    # Masked mean across sequence length dimension (ignore padding)
                    summed = (layer_tensor * mask).sum(dim=1)  # [B, D]
                    counts = mask.sum(dim=1).clamp(min=1.0)  # [B, 1]
                    embeddings = summed / counts  # [B, D]

                    # Emit one record per (sequence, layer)
                    for idx, seq in enumerate(batch):
                        mean_embedding = embeddings[idx]
                        record = {
                            "sequence_id": seq["sequence_id"],
                            "embedding_type_id": embedding_type_id,
                            "layer_index": li,  # propagated for DB storage and auditing
                            "sequence": seq["sequence"],  # original, unmodified AA string
                            "embedding": mean_embedding.cpu().numpy().tolist(),
                            "shape": mean_embedding.shape,
                        }
                        embedding_records.append(record)

            except Exception as e:
                # Continue with the next batch; free GPU cache on failure
                print(f"Error processing batch {i // batch_size}: {e}")
                torch.cuda.empty_cache()
                continue

    return embedding_records
