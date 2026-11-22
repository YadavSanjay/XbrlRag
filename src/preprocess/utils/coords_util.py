

def calculate_iou(box1: list[float], box2: list[float]) -> float:
        """Calculate Intersection over Union between two bounding boxes."""
        x1_min, y1_min, x1_max, y1_max = box1
        x2_min, y2_min, x2_max, y2_max = box2

        # Calculate intersection area
        inter_x_min = max(x1_min, x2_min)
        inter_y_min = max(y1_min, y2_min)
        inter_x_max = min(x1_max, x2_max)
        inter_y_max = min(y1_max, y2_max)

        if inter_x_max <= inter_x_min or inter_y_max <= inter_y_min:
            return 0.0

        inter_area = (inter_x_max - inter_x_min) * (inter_y_max - inter_y_min)

        # Calculate union area
        box1_area = (x1_max - x1_min) * (y1_max - y1_min)
        box2_area = (x2_max - x2_min) * (y2_max - y2_min)
        union_area = box1_area + box2_area - inter_area

        return inter_area / union_area if union_area > 0 else 0.0

def is_encapsulated(inner_box: list[float], outer_box: list[float], threshold: float = 0.9) -> bool:
        """Check if inner_box is mostly contained within outer_box."""
        x1_min, y1_min, x1_max, y1_max = inner_box
        x2_min, y2_min, x2_max, y2_max = outer_box

        # Calculate intersection area
        inter_x_min = max(x1_min, x2_min)
        inter_y_min = max(y1_min, y2_min)
        inter_x_max = min(x1_max, x2_max)
        inter_y_max = min(y1_max, y2_max)

        if inter_x_max <= inter_x_min or inter_y_max <= inter_y_min:
            return False

        inter_area = (inter_x_max - inter_x_min) * (inter_y_max - inter_y_min)
        inner_area = (x1_max - x1_min) * (y1_max - y1_min)

        # If most of the inner box is within the outer box, it's encapsulated
        return (inter_area / inner_area) >= threshold if inner_area > 0 else False
