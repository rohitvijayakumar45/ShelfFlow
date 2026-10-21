"""Train an explicitly unevaluated item-detector pilot from one collection session."""
import argparse
import json
from pathlib import Path

from ultralytics import YOLO
from ultralytics.models.yolo.detect import DetectionTrainer


class TrainOnlyPilot(DetectionTrainer):
    """Never calculate validation accuracy on training-session images."""
    def get_dataset(self):
        data = super().get_dataset()
        # Ultralytics requires a test loader during setup. This placeholder is
        # never scored: validate/final_eval below explicitly disable evaluation.
        data['val'] = data['train']
        return data

    def validate(self):
        return {}, -float(self.loss.detach().cpu().mean())

    def final_eval(self):
        self.metrics = {}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--data', default='data/shelf-pilot/pilot-labelled.yaml')
    parser.add_argument('--model', default='models/yolo26n-pretrained.pt')
    parser.add_argument('--epochs', type=int, default=40)
    parser.add_argument('--project', default='data/shelf-pilot/runs')
    parser.add_argument('--name', default='item-pilot')
    args = parser.parse_args()
    model = YOLO(args.model)
    model.train(trainer=TrainOnlyPilot, data=args.data, single_cls=True, device=0,
                epochs=args.epochs, imgsz=640, batch=4, workers=0, seed=42,
                project=str(Path(args.project).resolve()), name=args.name, exist_ok=False,
                split='train',
                val=False, plots=False, amp=False, patience=0, save=True,
                optimizer='AdamW', lr0=.001, mosaic=.3, close_mosaic=5)
    output = Path(model.trainer.save_dir)
    # Last fixed-epoch checkpoint, not an accuracy-selected "best" checkpoint.
    checkpoint = output / 'weights' / 'last.pt'
    if not checkpoint.is_file():
        raise RuntimeError('Training did not produce a checkpoint')
    report = {'status': 'TRAINED_UNEVALUATED_PILOT', 'checkpoint': str(checkpoint.resolve()),
              'epochs': args.epochs, 'classes': ['item'], 'accuracy': None,
              'validation': 'DISABLED: only one collection session is available',
              'selection': 'Final epoch, not best validation accuracy',
              'limitations': 'Three demo products; unseen-item generalisation unmeasured.'}
    (output / 'pilot-status.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
    print(json.dumps(report, indent=2))


if __name__ == '__main__':
    main()
