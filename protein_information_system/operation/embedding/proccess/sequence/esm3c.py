# esm3c.py — Interface for ESM-3c model, styled after esm.py

from esm.models.esmc import ESMC
from esm.sdk.api import ESMProtein, LogitsConfig
import torch


def load_model(model_name, conf):
    """
    Loads the pretrained ESM-3c model onto the specified device.

    Args:
        model_name (str): The name or path of the pretrained ESM-3c model.
        conf (dict): Configuration dictionary. Must contain the key 'embedding' → 'device'.

    Returns:
        torch.nn.Module: The ESM-3c model loaded on the specified device.
    """
    device = torch.device(conf["embedding"].get("device", "cuda"))
    return ESMC.from_pretrained(model_name).to(device)


def load_tokenizer(model_name=None):
    """
    Returns a placeholder tokenizer for API compatibility.

    ESM-3c does not require an explicit tokenizer. This function is provided
    to comply with interfaces expecting a tokenizer-loading function.

    Args:
        model_name (str, optional): Ignored.

    Returns:
        None
    """
    return None


def embedding_task(sequences, model, tokenizer, device, embedding_type_id=None):
    """
    Computes per-sequence embeddings using the ESM-3c model.

    This function encodes each input sequence individually using ESM-3c,
    extracting the mean of the residue-level embeddings to produce a fixed-length vector.

    Args:
        sequences (list of dict): List of dictionaries, each containing:
            - 'sequence' (str): Amino acid sequence.
            - 'sequence_id' (str or int): Unique identifier for the sequence.
        model (ESMC): Preloaded ESM-3c model.
        tokenizer (None): Unused placeholder to match pipeline interface.
        device (torch.device): Device on which to run the model (e.g., 'cuda' or 'cpu').
        embedding_type_id (int, optional): ID specifying the type of embedding (for tracking).

    Returns:
        list of dict: Each dictionary contains:
            - 'sequence_id': Identifier of the sequence.
            - 'embedding_type_id': Provided type ID (if any).
            - 'sequence': Original input sequence.
            - 'embedding': List[float] of the averaged embedding vector.
            - 'shape': Shape of the embedding tensor before flattening.
    """
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
                    LogitsConfig(sequence=True, return_embeddings=True, )
                )

                emb = logits_output.embeddings[0, 1:-1].mean(dim=0)  # [L, D] → [D]

                record = {
                    "sequence_id": sequence_id,
                    "embedding_type_id": embedding_type_id,
                    "sequence": sequence,
                    "embedding": emb.cpu().numpy().tolist(),
                    "shape": emb.shape
                }
                embedding_records.append(record)

            except Exception as e:
                print(f"❌ Failed to process sequence {sequence_id}: {e}")
                torch.cuda.empty_cache()
                continue

    return embedding_records
