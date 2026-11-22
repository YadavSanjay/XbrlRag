from __future__ import annotations

import argparse
import logging
from dataclasses import dataclass
from typing import List

import torch
from PIL import Image
from transformers import AutoImageProcessor
from transformers.models.detr import DetrForSegmentation

_DEFAULT_MODEL_ID = "cmarkea/detr-layout-detection"
_DEFAULT_THRESHOLD = 0.4
_LOGGER = logging.getLogger(__name__)


@dataclass
class Detection:
    """Represents a single layout detection returned by the DETR model."""
    label: str
    score: float
    box: List[float]


class LayoutDetector:
    """Performs layout detection using a DETR segmentation model."""

    def __init__(self, model_id: str = _DEFAULT_MODEL_ID) -> None:
        self._image_processor = AutoImageProcessor.from_pretrained(model_id)
        self._model = DetrForSegmentation.from_pretrained(model_id)

    def detect(self, image_path: str, threshold: float = _DEFAULT_THRESHOLD) -> List[Detection]:
        """Runs detection on a single image.

        Args:
            image_path: Absolute or relative path to the image file.
            threshold: Confidence threshold for detections.

        Returns:
            A list of detections sorted in the order returned by the model.
        """
        _LOGGER.debug("Opening image at %s", image_path)
        image = Image.open(image_path)

        _LOGGER.debug("Running inference with threshold=%s", threshold)
        with torch.inference_mode():
            inputs = self._image_processor(image, return_tensors="pt")
            outputs = self._model(**inputs)

        results = self._image_processor.post_process_object_detection(
            outputs,
            threshold=threshold,
            target_sizes=[image.size[::-1]],
        )[0]

        detections: List[Detection] = []
        for score, label, box in zip(results["scores"], results["labels"], results["boxes"]):
            detection = Detection(
                label=self._model.config.id2label[label.item()],
                score=round(score.item(), 3),
                box=[round(coord, 2) for coord in box.tolist()],
            )
            _LOGGER.debug("Detected %s", detection)
            detections.append(detection)
        return detections


def parse_args() -> argparse.Namespace:
    """Parses CLI arguments."""
    parser = argparse.ArgumentParser(description="Run DETR layout detection on an image.")
    parser.add_argument("image_path", help="Path to the input image.")
    parser.add_argument(
        "--threshold",
        type=float,
        default=_DEFAULT_THRESHOLD,
        help=f"Confidence threshold for detections (default: {_DEFAULT_THRESHOLD}).",
    )
    parser.add_argument(
        "--model-id",
        default=_DEFAULT_MODEL_ID,
        help=f"Model identifier to use (default: {_DEFAULT_MODEL_ID}).",
    )
    return parser.parse_args()


def main() -> None:
    """Script entry point."""
    logging.basicConfig(level=logging.INFO)
    args = parse_args()

    detector = LayoutDetector(model_id=args.model_id)
    detections = detector.detect(image_path=args.image_path, threshold=args.threshold)

    for detection in detections:
        print(
            f"Detected {detection.label} with confidence {detection.score} at location {detection.box}"
        )


if __name__ == "__main__":
    main()