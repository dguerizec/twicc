"""Shared text-filter grammar for frontend-equivalent backend filtering."""


def match_subsequence(query: str, text: str) -> bool:
    """Return whether every query character occurs in text, in order."""
    lower_query = query.lower()
    lower_text = text.lower()
    query_index = 0
    for character in lower_text:
        if query_index >= len(lower_query):
            break
        if character == lower_query[query_index]:
            query_index += 1
    return query_index == len(lower_query)


def match_text_query(query: str, text: str) -> bool:
    """Apply the shared fuzzy or leading-quote literal filter grammar."""
    if query and query[0] in ('"', "'"):
        quote = query[0]
        needle = query[1:]
        if needle.endswith(quote):
            needle = needle[:-1]
        if not needle:
            return True
        return needle.lower() in text.lower()
    return match_subsequence(query, text)
