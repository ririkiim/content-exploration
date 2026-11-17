import re
import unicodedata


def normalize_text(value):
    if not value:
        return ""

    value = unicodedata.normalize("NFKC", value)
    value = value.lower().strip()

    value = re.sub(r"\s+", "", value)
    value = re.sub(r"[^\w가-힣]", "", value)

    return value


def normalize_isbn(isbn):
    if not isbn:
        return None

    isbn = re.sub(r"[^0-9]", "", str(isbn))

    if len(isbn) != 13:
        return None

    digits = [int(x) for x in isbn]

    checksum = (
        10 - sum(
            digit * (1 if i % 2 == 0 else 3)
            for i, digit in enumerate(digits[:12])
        ) % 10
    ) % 10

    if checksum != digits[12]:
        return None

    return isbn


def is_possible_same_work(new_book, existing_book):
    new_title = normalize_text(new_book.get("title"))
    old_title = normalize_text(existing_book.get("title"))

    new_author = normalize_text(new_book.get("authors"))
    old_author = normalize_text(existing_book.get("authors"))

    if not all([new_title, old_title, new_author, old_author]):
        return False

    return (
        new_title == old_title
        and new_author == old_author
    )