"""Verify CUDA, torchvision detection kernels, and an optimizer step on GPU."""
import argparse
import json
from pathlib import Path


def check():
    import torch
    import torchvision
    if not torch.cuda.is_available():
        raise RuntimeError('CUDA is unavailable in this Python environment')
    device = torch.device('cuda:0')
    torch.manual_seed(42)
    model = torch.nn.Sequential(torch.nn.Conv2d(3, 8, 3), torch.nn.ReLU(),
                                torch.nn.AdaptiveAvgPool2d(1), torch.nn.Flatten(),
                                torch.nn.Linear(8, 3)).to(device)
    optimizer = torch.optim.AdamW(model.parameters(), lr=.001)
    inputs = torch.randn(4, 3, 64, 64, device=device)
    target = torch.tensor([0, 1, 2, 0], device=device)
    before = model[-1].weight.detach().clone()
    optimizer.zero_grad()
    loss = torch.nn.functional.cross_entropy(model(inputs), target)
    loss.backward()
    optimizer.step()
    if not torch.isfinite(loss) or torch.equal(before, model[-1].weight):
        raise RuntimeError('GPU training step failed')
    boxes = torch.tensor([[0, 0, 10, 10], [1, 1, 9, 9], [20, 20, 30, 30]],
                         dtype=torch.float32, device=device)
    keep = torchvision.ops.nms(boxes, torch.tensor([.9, .8, .7], device=device), .5)
    if keep.tolist() != [0, 2]:
        raise RuntimeError('GPU detection kernel check failed')
    torch.cuda.synchronize()
    return {'torch': torch.__version__, 'torchvision': torchvision.__version__,
            'cuda_runtime': torch.version.cuda, 'gpu': torch.cuda.get_device_name(0),
            'vram_bytes': torch.cuda.get_device_properties(0).total_memory,
            'gpu_optimizer_step': 'PASS', 'gpu_nms': 'PASS',
            'note': 'Synthetic compatibility check only; not model accuracy.'}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    result = check()
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(result, indent=2), encoding='utf-8')
    print(json.dumps(result, indent=2))
