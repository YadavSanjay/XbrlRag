
from sentence_transformers import SentenceTransformer, util

SEMANTICALLY_SIMILAR_MODEL_NAME = "all-MiniLM-L6-v2"


def are_strings_similar(str1, str2, threshold=0.8):
    """
    Compares two strings and returns True if they are similar based on the given threshold.
    Similarity is calculated using a simple ratio of matching characters to total characters.
    """
    if not str1 or not str2:
        return False

    str1 = str1.lower().strip()
    str2 = str2.lower().strip()

    # strip out "," and "and"  for better matching
    str1 = str1.replace(",", "").replace("and ", "")
    str2 = str2.replace(",", "").replace("and ", "").replace("[", "").replace("]", "")

    matches = sum(1 for a, b in zip(str1, str2) if a == b)
    total_length = max(len(str1), len(str2))

    similarity_ratio = matches / total_length
    return similarity_ratio >= threshold


def are_string_starting_similar(str1, str2, starting_length=8):
    """
    Compares the starting segments of two strings and returns True if they are similar based on the given threshold.
    Similarity is calculated using a simple ratio of matching characters to total characters in the shorter string.
    """
    if not str1 or not str2:
        return False
    if 'Item ' not in str2:
        str1 = str1[starting_length:]
    return are_strings_similar(str1, str2)


def are_strings_semantically_similar(str1, str2, threshold=0.7):
    if not str1 or not str2:
        return False
    model = SentenceTransformer(SEMANTICALLY_SIMILAR_MODEL_NAME)
    # For simplicity, we will just use the simple similarity check here.
    embedding1 = model.encode(str1, convert_to_tensor=True)
    embedding2 = model.encode(str2, convert_to_tensor=True)
    similarity = util.cos_sim(embedding1, embedding2).item()

    return similarity >= threshold


if __name__ == "__main__":
    s1 = "Item 15. Exhibits, Financial Statement Schedules"
    s2 = "ITEM 15. EXHIBITS AND FINANCIAL STATEMENT SCHEDULES"
    print(are_strings_similar(s1, s2))
