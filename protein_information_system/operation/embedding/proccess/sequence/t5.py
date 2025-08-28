# t5.py — Backend for computing embeddings with T5 encoder models
from transformers import T5Tokenizer, T5EncoderModel
import re
import torch

from protein_information_system.helpers.layers import validate_layer_indices


def load_model(model_name, conf):
    """
    Load a T5 encoder configured to return all hidden states.

    Args:
        model_name (str): Hugging Face model identifier or local path.
        conf (dict): Global configuration; must include 'embedding' → 'device'.

    Returns:
        T5EncoderModel: Model moved to the target device with appropriate dtype.
    """
    device = torch.device(conf['embedding'].get('device', "cuda"))
    dtype = torch.float16 if device.type == "cuda" else torch.float32

    # output_hidden_states=True → enables selection of arbitrary layers post forward
    return T5EncoderModel.from_pretrained(
        model_name,
        output_hidden_states=True,
        torch_dtype=dtype
    ).to(device)


def load_tokenizer(model_name):
    """
    Load the tokenizer associated with the T5 model.

    Args:
        model_name (str): Hugging Face model identifier or local path.

    Returns:
        T5Tokenizer
    """
    return T5Tokenizer.from_pretrained(model_name, do_lower_case=False)


def embedding_task(
        sequences,
        model,
        tokenizer,
        device,
        batch_size=32,
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
        model (T5EncoderModel): Preloaded T5 encoder (output_hidden_states=True).
        tokenizer (T5Tokenizer): Preloaded tokenizer.
        device (str or torch.device): 'cuda' or 'cpu'.
        batch_size (int): Number of sequences per batch.
        embedding_type_id (int | None): Embedding type identifier propagated downstream.
        layer_index_list (list[int] | None): Layers to export; default [0].

    Returns:
        list[dict]: One record per sequence per requested layer with fields:
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

    # Pre-tokenization normalization:
    #  - Uppercase → assume AA input; replace ambiguous U/Z/O/B with X; use "<AA2fold>"
    #  - Lowercase → assume fold-to-AA mode; use "<fold2AA>"
    sequences_processed = [
        {
            "sequence_id": seq["sequence_id"],
            "processed_sequence": (
                "<AA2fold> " + " ".join(list(re.sub(r"[UZOB]", "X", seq["sequence"])))
                if seq["sequence"].isupper()
                else "<fold2AA> " + " ".join(list(seq["sequence"]))
            )
        }
        for seq in sequences
    ]

    # Batch processing
    for i in range(0, len(sequences_processed), batch_size):
        batch_sequences = sequences_processed[i:i + batch_size]

        # Tokenize with dynamic padding to the longest sequence in the batch.
        # add_special_tokens=True ensures proper BOS/EOS handling for T5.
        inputs = tokenizer.batch_encode_plus(
            [seq["processed_sequence"] for seq in batch_sequences],
            padding="longest",
            truncation=False,
            add_special_tokens=True,
            return_tensors="pt"
        ).to(device)

        with torch.no_grad():
            try:
                # Single forward pass for the batch; re-use hidden_states for all requested layers.
                outputs = model(
                    input_ids=inputs.input_ids,
                    attention_mask=inputs.attention_mask
                )
                hidden_states = outputs.hidden_states  # tuple: [emb, layer1, ..., last]; shapes [B, L, D]

                # Build a broadcastable mask to exclude padded tokens from the mean.
                # attention_mask: [B, L] with 1 for valid tokens and 0 for padding.
                mask = inputs.attention_mask.unsqueeze(-1).type_as(hidden_states[-1])  # [B, L, 1]

                # ✅ Validate configured layer indices (reverse indexing: 0=last)
                valid_layers = validate_layer_indices(
                    layer_index_list,
                    hidden_states,
                    model_tag="T5",
                    sequence_id=f"batch-{i // batch_size}",
                )

                # Iterate over valid layers; compute masked mean per sequence for each layer.
                for li in valid_layers:
                    layer_tensor = hidden_states[-(li + 1)]  # [B, L, D]

                    # Masked mean across the sequence length dimension:
                    summed = (layer_tensor * mask).sum(dim=1)  # [B, D]
                    counts = mask.sum(dim=1).clamp(min=1.0)  # [B, 1]
                    embeddings = summed / counts  # [B, D]

                    # Materialize one record per (sequence, layer)
                    for idx, seq in enumerate(batch_sequences):
                        record = {
                            "sequence_id": seq["sequence_id"],
                            "embedding_type_id": embedding_type_id,
                            "layer_index": li,
                            "sequence": sequences[i + idx]["sequence"],  # original string
                            "embedding": embeddings[idx].cpu().numpy().tolist(),
                            "shape": embeddings[idx].shape,
                        }
                        embedding_records.append(record)

            except Exception as e:
                # Robustness: continue processing remaining batches; free GPU cache on failure.
                print(f"Error processing batch {i // batch_size}: {e}")
                torch.cuda.empty_cache()
                continue

    return embedding_records
