# esm.py — Backend for computing ESM embeddings with multi-layer export
from transformers import AutoTokenizer, EsmModel
import torch

from protein_information_system.helpers.layers import validate_layer_indices


def load_model(model_name, conf):
    """
    Load an EsmModel configured to return hidden states.

    Args:
        model_name (str): HF model id or local path (e.g., "facebook/esm2_t33_650M_UR50D").
        conf (dict): Global configuration; must include 'embedding' → 'device'.

    Returns:
        EsmModel: Model on the requested device with hidden-states enabled.
    """
    device = torch.device(conf['embedding'].get('device', "cuda"))
    # Enable hidden states so we can export arbitrary layers after a single forward
    return EsmModel.from_pretrained(model_name, output_hidden_states=True).to(device)


def load_tokenizer(model_name):
    """
    Load the tokenizer associated with the ESM model.
    """
    return AutoTokenizer.from_pretrained(model_name)


def _zero_special_tokens_inplace(attn_mask: torch.Tensor) -> torch.Tensor:
    """
    Zero out CLS/BOS (position 0) and EOS (last non-pad position) in the attention mask.

    Args:
        attn_mask (torch.Tensor): [B, L] with 1 for valid tokens and 0 for padding.

    Returns:
        torch.Tensor: Modified mask with CLS/BOS and EOS positions set to 0.
    """
    # CLS/BOS is at index 0 for ESM tokenization
    attn_mask[:, 0] = 0

    # Identify EOS as the last non-pad position per sequence: sum(mask)-1
    lengths = attn_mask.sum(dim=1)  # [B]
    # Guard against empty sequences (should not happen, but keep robust)
    lengths = torch.clamp(lengths, min=1)
    last_idx = (lengths - 1).to(torch.long)  # [B]
    # Batch-scatter to zero EOS
    batch_indices = torch.arange(attn_mask.size(0), device=attn_mask.device)
    attn_mask[batch_indices, last_idx] = 0

    return attn_mask


def embedding_task(
    sequences,
    model,
    tokenizer,
    device,
    batch_size="NOT_SUPPORTED",  # kept for API parity; ESM path encodes 1-by-1
    embedding_type_id=None,
    layer_index_list=None,
):
    """
    Compute per-sequence embeddings for one or more hidden-state layers in a single forward.

    Layer indexing convention (relative to the end):
        0 → last layer
        1 → penultimate layer
        2 → antepenultimate layer
        ...

    Args:
        sequences (list[dict]): Each item must contain:
            - 'sequence'    (str): Amino-acid sequence.
            - 'sequence_id' (int|str): Unique identifier.
        model (EsmModel): Preloaded ESM model with output_hidden_states=True.
        tokenizer: Preloaded tokenizer.
        device (str|torch.device): 'cuda' or 'cpu'.
        batch_size: Unused here; ESM encode is done per sequence to avoid OOM.
        embedding_type_id (int|None): Embedding type identifier to propagate.
        layer_index_list (list[int]|None): Layers to export; default [0].

    Returns:
        list[dict]: One record per (sequence, layer) with fields:
            - sequence_id
            - embedding_type_id
            - layer_index
            - sequence
            - embedding (list[float])
            - shape (torch.Size)
    """
    if layer_index_list is None:
        layer_index_list = [0]

    model.to(device)
    embedding_records = []

    with torch.no_grad():
        for seq_info in sequences:
            sequence = seq_info["sequence"]
            sequence_id = seq_info.get("sequence_id")

            # Tokenize one sequence (dynamic length)
            tokens = tokenizer(
                sequence,
                return_tensors="pt",
                truncation=False,
                padding="longest",
                add_special_tokens=True,
            )
            tokens = {k: v.to(device) for k, v in tokens.items()}

            try:
                # Single forward; reuse hidden states for all requested layers
                outputs = model(**tokens)
                hidden_states = outputs.hidden_states  # tuple of [B, L, D] tensors

                # ✅ Validate configured layer indices (reverse indexing: 0=last)
                valid_layers = validate_layer_indices(
                    layer_index_list,
                    hidden_states,
                    model_tag="ESM",
                    sequence_id=sequence_id,
                )

                # Build a mask that excludes padding AND special tokens (CLS/BOS, EOS)
                mask = tokens["attention_mask"].clone()  # [B, L]
                mask = _zero_special_tokens_inplace(mask)  # zero CLS/BOS and EOS
                mask_f = mask.unsqueeze(-1).type_as(hidden_states[-1])  # [B, L, 1]

                for li in valid_layers:
                    # 0=last, 1=penultimate, etc. → python negative index
                    layer_tensor = hidden_states[-(li + 1)]  # [B, L, D]

                    # Masked mean over sequence length (ignore padding + specials)
                    summed = (layer_tensor * mask_f).sum(dim=1)  # [B, D]
                    counts = mask_f.sum(dim=1).clamp(min=1.0)  # [B, 1]
                    mean_embedding = (summed / counts)[0]  # [D]; B==1

                    record = {
                        "sequence_id": sequence_id,
                        "embedding_type_id": embedding_type_id,
                        "layer_index": li,
                        "sequence": sequence,
                        "embedding": mean_embedding.detach().cpu().numpy().tolist(),
                        "shape": mean_embedding.shape,
                    }
                    embedding_records.append(record)

            except Exception as e:
                print(f"Failed to process sequence {sequence_id}: {e}")
                torch.cuda.empty_cache()
                continue

    return embedding_records
