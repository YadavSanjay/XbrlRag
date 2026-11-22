import json
from pathlib import Path
from typing import Any, Dict, List


class Sec10kPageSummaryReader:
    """Reads page summaries from text files containing JSON data."""

    REQUIRED_KEYS = {'summary', 'keywords', 'section'}

    def read_summary(self, file_path: str) -> dict[str, Any]:
        """
        Read and parse a summary file containing JSON data.
        
        Args:
            file_path: Path to the text file containing JSON summary
            
        Returns:
            Dictionary containing summary, keywords, and section
            
        Raises:
            FileNotFoundError: If the file doesn't exist
            json.JSONDecodeError: If the file doesn't contain valid JSON
            ValueError: If required keys are missing from the JSON data
        """
        path = Path(file_path)

        if not path.exists():
            raise FileNotFoundError(f"Summary file not found: {file_path}")

        content = path.read_text(encoding='utf-8')
        data = json.loads(content)

        # Validate required keys are present
        missing_keys = self.REQUIRED_KEYS - set(data.keys())
        if missing_keys:
            raise ValueError(f"Missing required keys in JSON: {', '.join(sorted(missing_keys))}")

        return data
