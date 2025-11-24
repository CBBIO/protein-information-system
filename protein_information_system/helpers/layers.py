# helpers/layers.py
from typing import Iterable, List, Optional, Sequence

try:
    import torch
except ImportError:
    torch = None


def validate_layer_indices(
        layer_index_list: Iterable[int],
        hidden_states: Optional[Sequence],
        *,
        model_tag: str,
        sequence_id: str | int,
) -> List[int]:
    """
    Validate user-provided layer indices against the number of available hidden states.

    - 0 = last layer, 1 = penultimate, etc.
    - Raises ValueError if any index is out of range.
    - Returns sorted unique valid indices.
    """
    if hidden_states is None:
        # embeddings-only: only 0 is valid
        total_layers = 1
    elif (torch is not None) and isinstance(hidden_states, torch.Tensor):
        total_layers = int(hidden_states.shape[0])  # e.g. ESM3c returns [N_layers, ...]
    else:
        total_layers = len(hidden_states)  # Transformers: tuple/list

    allowed = range(0, total_layers)
    req = sorted(set(int(li) for li in layer_index_list))
    invalid = [li for li in req if li not in allowed]

    if invalid:
        raise ValueError(
            f"[{model_tag}] sequence_id={sequence_id}: invalid layer_index {invalid}. "
            f"Allowed range: 0..{total_layers - 1}; total_layers={total_layers}."
        )

    return req
