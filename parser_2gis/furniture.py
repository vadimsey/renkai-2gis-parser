"""Require an explicit furniture-cleaning phrase within one piece of evidence."""
import re


def furniture_service(texts):
    for text in texts:
        value = str(text or '').casefold().replace('ё', 'е')
        if (re.search(r'(?:химчист|чистк|чисти|очист)', value)
                and re.search(r'(?:мебел|диван|матрас|кресел|кресл|стул|обивк)', value)):
            return True
    return False
