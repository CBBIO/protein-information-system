# Usa la imagen más reciente de NVIDIA CUDA con Ubuntu 22.04
FROM nvidia/cuda:12.3.0-runtime-ubuntu22.04

# Establece el directorio de trabajo
WORKDIR /app


RUN apt-get update && apt-get install -y \
    python3 \
    python3-pip \
    && rm -rf /var/lib/apt/lists/*

COPY . .

RUN pip3 install --upgrade pip
RUN pip3 install  protein-information-system
RUN pip3 install flash-attn --no-build-isolation



# Configura el comando predeterminado para ejecutar tu paquete
CMD ["python3", "-m", "protein_information_system"]
