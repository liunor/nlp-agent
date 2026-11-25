"""Match bounded content slices, including continuation boundaries."""

def search_blocks(blocks, query, *, unit):
    hits = []
    for block in blocks:
        text = block['text']
        index = text.casefold().find(query.casefold())
        if index >= 0:
            hits.append(dict(unit=unit, number=block['number'],
                             text=text[max(0, index-100):index+len(query)+300]))
    return hits
