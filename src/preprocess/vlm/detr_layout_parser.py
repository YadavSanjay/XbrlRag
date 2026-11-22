"""Utilities for extracting table detections with DETR layout models."""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List

import torch
from PIL import Image
from transformers import AutoImageProcessor
from transformers.models.detr import DetrForSegmentation

from src import BASE_DATA_DIR
from src.logging_config import get_logger
from src.preprocess.utils.coords_util import calculate_iou, is_encapsulated

LOGGER = get_logger(__name__)

MODEL_ID = "cmarkea/detr-layout-detection"
TABLE_LABEL = "Table"


@dataclass(frozen=True)
class TableDetection:
    """Container for a single table detection."""
    score: float
    box: torch.Tensor


def extract_and_save_tables_from_image(
    year: str,
    folder_with_images: str,
    page_number: int = -1,
    threshold: float = 0.8,
    box_buffer: float = 10.0,
) -> None:
    """Extract table detections for each ordered page image in a folder.

    Args:
        year: Year subfolder under BASE_DATA_DIR.
        folder_with_images: Name of the folder under BASE_DATA_DIR to process.
        page_number: Specific page to process, or -1 for all pages.
        threshold: Minimum detection confidence to keep.
        box_buffer: Extra padding (in pixels) applied to each bounding box.

    Returns:
        None

    Raises:
        FileNotFoundError: If the expected image directory is missing.
        ValueError: If any detection box does not contain four coordinates.
    """
    image_dir = BASE_DATA_DIR / year / folder_with_images / "images"
    if not image_dir.is_dir():
        msg = f"Image directory not found: {image_dir}"
        raise FileNotFoundError(msg)

    processor = AutoImageProcessor.from_pretrained(MODEL_ID)
    model = DetrForSegmentation.from_pretrained(MODEL_ID)
    model.eval()

    ordered_images = []
    if page_number == -1:
        image_pattern = re.compile(rf"{re.escape(folder_with_images)}_page_(\d+)\.jpg")
        ordered_images = _iter_ordered_images(image_dir, image_pattern)
        if not ordered_images:
            LOGGER.warning("No images matched the expected pattern in %s", image_dir)
            return {}
    else:
        image_path = image_dir / f"{folder_with_images}_page_{page_number}.jpg"
        if image_path.is_file():
            ordered_images = [(page_number, image_path)]

    for page_number, image_path in ordered_images:
        LOGGER.info("Processing page %d (%s)", page_number, image_path)
        with Image.open(image_path) as image:
            with torch.inference_mode():
                inputs = processor(image, return_tensors="pt")
                outputs = model(**inputs)

        object_detections = processor.post_process_object_detection(
            outputs,
            threshold=threshold,
            target_sizes=[image.size[::-1]],
        )[0]

        tables: list[TableDetection] = []
        for score_tensor, label_tensor, box_tensor in zip(
            object_detections["scores"],
            object_detections["labels"],
            object_detections["boxes"],
        ):
            label_name = model.config.id2label[label_tensor.item()]
            if label_name != TABLE_LABEL:
                continue

            box = _expand_box(box_tensor, box_buffer) if box_buffer else box_tensor
            tables.append(TableDetection(score=float(score_tensor), box=box))
        LOGGER.info("Page %d: Identified %d candidate tables", page_number, len(tables))
        tables = _dedup_identified_candidate_tables(page_number, tables)
        LOGGER.info("Page %d: Identified %d tables after deduplication", page_number, len(tables))
        _save_identified_table_images(year, folder_with_images, page_number, image_path, box_buffer, tables)
    return


def _iter_ordered_images(
    image_dir: Path,
    pattern: re.Pattern[str],
) -> list[tuple[int, Path]]:
    """Return image paths sorted by page number extracted from the filename."""
    numbered_files: list[tuple[int, Path]] = []
    for path in image_dir.glob("*.jpg"):
        match = pattern.match(path.name)
        if match:
            numbered_files.append((int(match.group(1)), path))
    numbered_files.sort(key=lambda item: item[0])
    return numbered_files


def _expand_box(box: torch.Tensor, padding: float) -> torch.Tensor:
    """Apply symmetrical padding to a 4-element bounding box tensor."""
    if box.numel() != 4:
        msg = "Bounding box tensor must contain exactly four elements."
        raise ValueError(msg)
    expanded_box = box.clone()
    expanded_box[0] -= padding
    expanded_box[1] -= padding
    expanded_box[2] += padding
    expanded_box[3] += padding
    return expanded_box


def _dedup_identified_candidate_tables(page_number: int, tables: list[TableDetection]) -> list[TableDetection]:
    """Remove duplicate and overlapping table detections, keeping highest scoring ones.
    
    Args:
        page_number: The page number being processed (for logging).
        tables: List of table detections to deduplicate.
        
    Returns:
        Deduplicated list of table detections.
    """
    if not tables:
        return tables

    # Sort by score descending to prioritize higher confidence detections
    sorted_tables = sorted(tables, key=lambda t: t.score, reverse=True)


    deduplicated: list[TableDetection] = []
    iou_threshold = 0.5  # Overlap threshold for considering boxes as duplicates

    for current_table in sorted_tables:
        should_keep = True

        for existing_table in deduplicated:
            iou = calculate_iou(current_table.box.tolist(), existing_table.box.tolist())

            # Check if current table overlaps significantly with an existing one
            if iou > iou_threshold:
                # Keep the one with higher score (existing already has higher score)
                should_keep = False
                LOGGER.info(
                    "Page %d: Removing overlapping table (score=%.3f) in favor of higher scoring one (score=%.3f), IoU=%.3f",
                    page_number, current_table.score, existing_table.score, iou
                )
                break

            # Check if current (higher score) is encapsulated in existing (lower score)
            # This shouldn't happen as we process high scores first, but check anyway
            if is_encapsulated(current_table.box.tolist(), existing_table.box.tolist()):
                # Keep the higher scoring smaller table, remove the larger one
                deduplicated.remove(existing_table)
                LOGGER.info(
                    "Page %d: Removing encapsulating table (score=%.3f) in favor of encapsulated higher scoring table (score=%.3f)",
                    page_number, existing_table.score, current_table.score
                )
                # Continue checking against other existing tables
                continue

        if should_keep:
            # Also check if any existing lower-score table encapsulates this one
            to_remove = []
            for existing_table in deduplicated:
                if is_encapsulated(current_table.box.tolist(), existing_table.box.tolist()):
                    # Current has higher score and is encapsulated, remove the larger one
                    to_remove.append(existing_table)
                    LOGGER.info(
                        "Page %d: Removing encapsulating table (score=%.3f) in favor of encapsulated higher scoring table (score=%.3f)",
                        page_number, existing_table.score, current_table.score
                    )

            for table in to_remove:
                deduplicated.remove(table)

            deduplicated.append(current_table)

    if len(deduplicated) < len(tables):
        LOGGER.info(
            "Page %d: Deduplicated %d tables to %d",
            page_number, len(tables), len(deduplicated)
        )

    return deduplicated


def _save_identified_table_images(
    year: str,
    folder_with_images: str,
    page_number: int,
    image_path: Path,
    box_buffer: float,
    detections: list[TableDetection],
    image_format: str = "jpeg",
) -> None:

    """
    Crop and persist detected table regions from a page image and write per-table metadata.
    For each detection in `detections`, this function:
    - Creates the output directory: BASE_DATA_DIR/<folder_with_images>/tables/page_<page_number> (if needed).
    - Crops the table from `image_path` using its bounding box [x_min, y_min, x_max, y_max].
    - Saves the crop as "table_<n>.<image_format>" where n starts at 1.
    - Writes a "table_<n>_metadata.txt" file containing comma-separated values:
        <score_rounded_to_3_decimals>,<box_buffer>,<x_min_rounded_to_2_decimals>,<y_min_rounded_to_2_decimals>,<x_max_rounded_to_2_decimals>,<y_max_rounded_to_2_decimals>.
    If `detections` is empty, the function logs an informational message and returns without writing files.
    Parameters:
            folder_with_images (str): Relative folder under BASE_DATA_DIR where results are written.
            page_number (int): Page index used to name the output subfolder (page_<page_number>).
            image_path (Path): Path to the source page image to crop.
            box_buffer (float): Buffer value associated with the detection boxes. It is not applied to the crops here; it is recorded in metadata for traceability.
            detections (List[TableDetection]): Detected tables. Each detection must provide:
                    - box: sequence-like of [x_min, y_min, x_max, y_max] in pixel coordinates (supports .tolist()).
                    - score: numeric confidence (supports .item()).
            image_format (str, optional): Output image format/extension for crops (e.g., "jpeg", "png"). Defaults to "jpeg".
    Returns:
            None
    Side effects:
            - Creates directories under BASE_DATA_DIR/<folder_with_images>/tables/page_<page_number>.
            - Writes cropped table images and per-table metadata files to disk.
            - Emits log messages via LOGGER.
    Raises:
            OSError: If the image cannot be opened or files cannot be written.
            ValueError: If a detection provides an invalid bounding box for PIL.Image.crop.
    """

    if not detections:
        LOGGER.info("Page %d: No table detections found", page_number)
        return

    tables_dir = BASE_DATA_DIR / year / folder_with_images / "tables"
    if not os.path.exists(tables_dir):
        os.mkdir(tables_dir)
    image_table_folder = tables_dir / f"page_{page_number}"
    if not os.path.exists(image_table_folder):
        os.makedirs(image_table_folder)

    with Image.open(image_path) as image:
        table_counter = 0
        for table in detections:
            table_counter += 1
            box = [round(i, 2) for i in table.box.tolist()]
            cropped_image = image.crop(box)
            cropped_image.save(f"{image_table_folder}/table_{table_counter}.{image_format}")
            table_metadata_path = tables_dir / f"page_{page_number}" / f"table_{table_counter}_metadata.txt"
            with table_metadata_path.open("w", encoding="utf8") as meta_file:
                meta_data = str(round(table.score, 3)) + "," + str(box_buffer) + "," \
                    + str(box[0]) + "," + str(box[1]) + "," + str(box[2]) + "," + str(box[3])
                meta_file.write(meta_data)
    LOGGER.info("saved identified tables to %s", image_table_folder)


if __name__ == "__main__":
    years = ["2023","2022"]
    for year in years:
        base_path = BASE_DATA_DIR / year
        #folders = [f for f in os.listdir(base_path) if os.path.isdir(os.path.join(base_path, f))]
        folders = [f"{year}-10K-INFOTECH-NVDA", f"{year}-10K-INFOTECH-AMD", f"{year}-10K-INFOTECH-AVGO"]
        for folder in folders:
            LOGGER.info("Processing directory: %s", folder)
            # Example usage
            extract_and_save_tables_from_image(year, folder)
