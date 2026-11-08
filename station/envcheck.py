"""Kurulumdan sonra, ComfyUI başlamadan önce Python ortamını doğrular (runtime_setup.sh çağırır).

Kullanım: python envcheck.py <beklenen torch sürümü> <beklenen Python sürümü> <çıktı.json>

Eklenti kodu çalıştırılmaz; yalnızca PyPI paketleri içe aktarılır. Her sorun toplanıp birlikte
yazılır ve çıkış kodu 1 olur; böylece kullanıcı "ComfyUI süreci öldü" ve uzun bir iz yerine hangi
paketin neden sorunlu olduğunu görür. Sürüm bilgisi json'a yazılır (bir şey kırılınca ilk bakılacak yer).
"""

import importlib
import json
import platform
import sys
from importlib import metadata

# ComfyUI iş akışlarının ve ses motorlarının ikili (derlenmiş) olarak kullandığı paketler.
MODULES = [
    "torch", "torchvision", "torchaudio", "numpy", "scipy", "numba", "llvmlite", "librosa", "soundfile",
    "sageattention", "transformers", "safetensors", "aiohttp", "av", "PIL", "kornia", "onnxruntime",
    "resampy", "diffq", "pyworld", "accelerate", "cv2",
]
# Sürümü kayda geçecek paketler (dağıtım adlarıyla).
RECORDED = ["torch", "torchvision", "torchaudio", "torchcodec", "numpy", "numba", "sageattention", "transformers",
            "huggingface-hub", "librosa", "triton", "opencv-python-headless", "nvidia-cudnn-cu13"]


def main(expected_torch, expected_python, out_path):
    problems = []
    if not platform.python_version().startswith(expected_python + "."):
        problems.append(f"Python {platform.python_version()} (beklenen {expected_python}.x)")
    failed = set()
    for name in MODULES:
        try:
            importlib.import_module(name)
        except Exception as e:  # ikili paketler ImportError dışında da düşebilir (OSError, RuntimeError)
            failed.add(name)
            problems.append(f"{name} içe aktarılamadı: {type(e).__name__}: {str(e)[:300]}")
    info = {"python": platform.python_version(), "packages": {}}
    for dist in RECORDED:
        try:
            info["packages"][dist] = metadata.version(dist)
        except metadata.PackageNotFoundError:
            info["packages"][dist] = None
    if "torch" not in failed:
        import torch
        info["torch_cuda"] = torch.version.cuda
        if not torch.__version__.startswith(expected_torch + "+"):
            problems.append(f"torch {torch.__version__} (beklenen {expected_torch})")
        if torch.cuda.is_available():
            info["gpu"] = torch.cuda.get_device_name(0)
            info["gpu_capability"] = "%d.%d" % torch.cuda.get_device_capability(0)
        else:
            problems.append(
                f"torch CUDA {torch.version.cuda} ile derlenmiş ama GPU görünmüyor: Colab sürücüsü bu CUDA sürümünü "
                "desteklemiyor olabilir (nvidia-smi çıktısına bakın)")
    with open(out_path, "w") as f:
        json.dump({**info, "problems": problems}, f, indent=1)
    if problems:
        print("ORTAM SORUNLARI:", file=sys.stderr)
        for p in problems:
            print("  - " + p, file=sys.stderr)
        return 1
    pk = info["packages"]
    print(f"ortam tamam: Python {info['python']}, torch {pk['torch']} (CUDA {info.get('torch_cuda')}), "
          f"sageattention {pk['sageattention']}, GPU {info.get('gpu')} (sm_{info.get('gpu_capability', '?').replace('.', '')})")
    return 0


if __name__ == "__main__":
    sys.exit(main(*sys.argv[1:4]))
