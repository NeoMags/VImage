# python/ivcore
Pipeline library used by the sidecar: file identification, ExifTool/Pillow metadata, SHA-256 + pHash, pyvips
thumbnails, OCR adapters (PaddleOCR, Tesseract), embedding adapter (OpenCLIP via ONNX Runtime), optional local
vision-LLM adapter (Ollama). Kept separate from the sidecar so stages are unit-testable without the API.
