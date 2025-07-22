from transformers import T5Tokenizer, T5EncoderModel
import torch
import numpy as np
import gc


def load_model(model_name, conf):
    device = torch.device(conf['embedding'].get('device', "cuda"))
    dtype = torch.float16 if device.type == "cuda" else torch.float32
    model = T5EncoderModel.from_pretrained(model_name, torch_dtype=dtype).to(device).eval()
    return model


def load_tokenizer(model_name):
    return T5Tokenizer.from_pretrained(model_name)


def embedding_task(sequences, model, tokenizer, device, batch_size=8, embedding_type_id=None):
    embedding_records = []

    for i in range(0, len(sequences), batch_size):
        batch = sequences[i:i + batch_size]
        protein_sequences = ["[NLU]" + seq["sequence"] for seq in batch]

        inputs = tokenizer.batch_encode_plus(
            protein_sequences,
            add_special_tokens=True,
            return_tensors="pt",
            padding=True,
            truncation=False,
        ).to(device)

        with torch.no_grad():
            try:
                outputs = model(**inputs)
                embeddings = outputs.last_hidden_state

                for idx, seq in enumerate(batch):
                    length = inputs.attention_mask[idx].sum().item() - 2  # excluye [NLU] y </s>
                    mean_embedding = embeddings[idx, 1:1 + length].mean(dim=0)

                    record = {
                        "sequence_id": seq["sequence_id"],
                        "embedding_type_id": embedding_type_id,
                        "sequence": seq["sequence"],
                        "embedding": mean_embedding.cpu().numpy().tolist(),
                        "shape": mean_embedding.shape
                    }
                    embedding_records.append(record)

            except Exception as e:
                print(f"⚠️ Error en batch {i // batch_size}: {e}")
                continue

    return embedding_records
