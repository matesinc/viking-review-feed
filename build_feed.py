#!/usr/bin/env python3
"""Build the Viking review feed (a Klaviyo web feed) from Lipscore Rating events.

Usage:
  python3 build_feed.py <dir of event pages *.json> <out.json> [--cache pages.json]
  KLAVIYO_API_KEY=pk_... python3 build_feed.py --klaviyo <out.json> [--cache pages.json]

Input: Klaviyo events of the metric "Lipscore Rating" (VWcacw), either saved pages (MCP
       get_events output, {result: {data: [...]}}) or pulled live from the Klaviyo API
       with a private key that has events:read. Stdlib only.
Output: {<profile Language>: {<first cart category>: {"headline": str, "reviews": [review]}}}
        Categories: kids, women, men, clothing, outlet, all. The template looks up
        person.Language, then event.Items.0.Categories.0, and picks one review with |random
        (snippet.html in this folder).
Rules (decision log, 2026-10-01): rating 4-5, verbatim text, the customer's own language
first and English second, product photo from the product page; a review whose product page
is gone, or whose product has no size in stock (2026-10-30), is dropped. Stock changes daily,
so every page is fetched again on each run; the cache only covers a failed fetch. `url` has no protocol: Stripo's link field adds https:// itself."""
import glob, html, json, re, sys, urllib.error, urllib.request, urllib.parse, datetime, os

METRIC = 'VWcacw'                   # Lipscore Rating
MIN_CHARS, MAX_CHARS = 40, 220      # short enough for the card, long enough to say something
MAX_PER_POOL = 8                    # newest first
LANGS = ['en', 'no', 'sv', 'da', 'fi', 'de']
COUNTRY = {'no': 'Norway', 'sv': 'Sweden', 'da': 'Denmark', 'fi': 'Finland', 'de': 'Germany'}
CDN = 'https://nvfevo.stripocdn.email/content/guids/CABINET_545cd1214b29809dc55d578dc752da037182cb01a24a1c8b3c22d892c525b059/images/'
STARS = {5: CDN + '5.png', 4: CDN + '4.png'}  # Viking's own star images, 430x70; 45.png (4.5) is for averages
# First category of the first cart line (200 carts, 2026-10-01): kids, men, clothing, women, outlet.
# 'men' comes first on adult unisex shoes, which also carry 'women'.
# Each value is a list of tiers: the first tier that has reviews wins.
ALL = ['kids', 'women', 'men', 'apparel']
POOLS = {'kids': [['kids']], 'women': [['women']], 'men': [['men'], ['women']],
         'clothing': [['apparel'], ['kids']], 'outlet': [ALL], 'all': [ALL]}
GENERIC = 'What customers say'
HEADLINE = {'kids': GENERIC + ' about kids’ footwear', 'women': GENERIC + ' about women’s footwear',
            'men': GENERIC + ' about men’s footwear', 'apparel': GENERIC + ' about our clothing'}

# Not praise: return or exchange requests and questions (they do slip through at 4 stars).
BLOCK = re.compile(r'\b(bytte|byte|returner|retur|return|exchange|umtausch|zur\u00fcck|palaut|vaihtaa)\w*|\?\s*$', re.I)

STOP = {
    'en': 'the and is are was with for this that they very my have has not but boots shoes good great perfect love happy',
    'no': 'og er det ikke jeg en et på til med for som har var de disse veldig fornøyd sko støvler bra',
    'sv': 'och är det inte jag en ett på till med för som har var de dessa mycket nöjd skor bra väldigt',
    'da': 'og er det ikke jeg en et på til med for som har var de disse meget støvler god dejlig',
    'fi': 'ja on ei että se olen ovat hyvä kengät todella tämä kanssa mutta kuin',
    'de': 'und ist das nicht ich ein eine auf mit für die der sehr gut schuhe stiefel wie',
}
STOP = {k: set(v.split()) for k, v in STOP.items()}

def language(text, form_lang):
    """Language of the text itself. Lipscore's tag is the review form's language: English text
    arrives tagged no/fi/sv, and Norwegian text tagged en. So: detect English from the words,
    otherwise trust the form's language, and when the form was English pick the best match."""
    words = re.findall(r"[a-zåäöæøüß']+", text.lower())
    score = {k: sum(w in s for w in words) for k, s in STOP.items()}
    others = max(v for k, v in score.items() if k != 'en')
    if score['en'] >= 2 and score['en'] > others: return 'en'
    if form_lang in STOP and form_lang != 'en': return form_lang
    rest = {k: v for k, v in score.items() if k != 'en'}
    return max(rest, key=rest.get) if others else None

def audience_from_category(cat):
    c = (cat or '').lower()
    if re.search(r'kid|junior|toddler', c): return 'kids'
    if 'women' in c: return 'women'
    if re.search(r'\bmen\b', c): return 'men'
    return None

def in_stock(offers):
    """True when any size is InStock. The offers are one per size (schema.org Offer)."""
    if isinstance(offers, dict): offers = offers.get('offers') or [offers]
    return any('InStock' in str(o.get('availability', '')) for o in offers or [])

def product_page(url, cache, fetched):
    """Audience, photo and stock from the product page; None when the page is gone."""
    if url in fetched: return cache.get(url)
    fetched.add(url)
    out = None
    try:
        req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
        page = urllib.request.urlopen(req, timeout=25).read().decode('utf-8', 'ignore')
        for m in re.finditer(r'<script type="application/ld\+json">(.*?)</script>', page, re.S):
            try: d = json.loads(m.group(1))
            except ValueError: continue
            if isinstance(d, dict) and d.get('@type') == 'Product' and d.get('image'):
                path = d.get('@id', '')
                aud = next((a for a in ('kids', 'women', 'men') if f'/{a}/' in path), None)
                if '/clothing' in path or '/apparel' in path: aud = 'apparel'
                img = re.sub(r'-\d+x\d+(\.\w+)$', r'-square\1', d['image'][0])   # 900x900 rendition
                out = {'audience': aud, 'image': img, 'url': path or url, 'in_stock': in_stock(d.get('offers'))}
                break
    except urllib.error.HTTPError as err:
        if err.code not in (404, 410): return cache.get(url)   # a hiccup: keep yesterday's answer
    except Exception:
        return cache.get(url)
    cache[url] = out
    return out

def events_from_dir(src):
    for f in sorted(glob.glob(os.path.join(src, '*.json'))):
        d = json.load(open(f))
        yield from d.get('result', d)['data']

def events_from_klaviyo():
    key = os.environ['KLAVIYO_API_KEY']
    q = urllib.parse.urlencode({'filter': f'equals(metric_id,"{METRIC}")', 'sort': '-datetime', 'page[size]': 200})
    url = 'https://a.klaviyo.com/api/events?' + q
    while url:
        req = urllib.request.Request(url, headers={'Authorization': f'Klaviyo-API-Key {key}',
                                                  'revision': '2024-10-15', 'accept': 'application/vnd.api+json'})
        d = json.load(urllib.request.urlopen(req, timeout=60))
        yield from d['data']
        url = d.get('links', {}).get('next')

def main():
    args = sys.argv[1:]
    cache_path = args[args.index('--cache') + 1] if '--cache' in args else None
    live = '--klaviyo' in args
    rest = [a for a in args if a not in ('--cache', '--klaviyo', cache_path)]
    events = events_from_klaviyo() if live else events_from_dir(rest[0])
    dst = rest[-1]
    cache = json.load(open(cache_path)) if cache_path and os.path.exists(cache_path) else {}
    reviews, seen, fetched = [], set(), set()
    dropped = {'not_praise': 0, 'short_or_long': 0, 'service': 0, 'no_page': 0, 'sold_out': 0, 'no_audience': 0, 'duplicate': 0}
    for e in events:
        p = e['attributes']['event_properties']
        if not p.get('has_review') or (p.get('rating') or 0) < 4: continue
        text = re.sub(r'\s+', ' ', html.unescape(re.sub(r'<[^>]+>', ' ', p.get('review.text') or ''))).strip()
        if p.get('is_service') or not p.get('product.url') or 'vikingfootwear.com/p/' not in p['product.url']:
            dropped['service'] += 1; continue
        if not MIN_CHARS <= len(text) <= MAX_CHARS: dropped['short_or_long'] += 1; continue
        if BLOCK.search(text): dropped['not_praise'] += 1; continue
        if text.lower() in seen: dropped['duplicate'] += 1; continue
        seen.add(text.lower())
        page = product_page(p['product.url'], cache, fetched)
        if not page: dropped['no_page'] += 1; continue
        if not page.get('in_stock'): dropped['sold_out'] += 1; continue
        aud = page['audience'] or audience_from_category(p.get('product.category'))
        if not aud: dropped['no_audience'] += 1; continue
        day = datetime.date.fromisoformat(p['created_at'][:10])
        reviews.append({
            'text': text, 'rating': p['rating'], 'stars': STARS[p['rating']],
            'product': p.get('product.name'), 'url': re.sub(r'^https?://', '', p['product.url']), 'image': page['image'],
            'date': f"{day.day} {day.strftime('%b %Y')}", 'country': COUNTRY.get(p.get('language'), ''),
            '_aud': aud, '_lang': language(text, p.get('language')), '_sort': p['created_at']})
    reviews.sort(key=lambda r: r['_sort'], reverse=True)
    if cache_path: json.dump(cache, open(cache_path, 'w'), indent=0)

    def pool(tiers, lang):
        """Per tier: the customer's own language first, then English. Never a third language.
        The headline names the audience only when the first tier answered."""
        for i, auds in enumerate(tiers):
            own = [r for r in reviews if r['_aud'] in auds]
            pick = [r for r in own if r['_lang'] == lang] or [r for r in own if r['_lang'] == 'en']
            if pick:
                head = HEADLINE[auds[0]] if i == 0 and len(auds) == 1 else GENERIC
                return {'headline': head,
                        'reviews': [{k: v for k, v in r.items() if not k.startswith('_')} for r in pick[:MAX_PER_POOL]]}
        return None

    feed = {}
    for lang in LANGS:
        feed[lang] = {cat: pool(tiers, lang) for cat, tiers in POOLS.items()}
        for cat in POOLS:                      # an empty pool borrows 'all', so the block only hides
            feed[lang][cat] = feed[lang][cat] or feed[lang]['all'] or {'headline': GENERIC, 'reviews': []}
    json.dump(feed, open(dst, 'w'), ensure_ascii=False, indent=1)
    print('kept', len(reviews), 'dropped', dropped, 'bytes', os.path.getsize(dst))
    for lang in LANGS:
        print(lang, {k: len(v['reviews']) for k, v in feed[lang].items()})
    print('languages of kept reviews', {l: sum(r['_lang'] == l for r in reviews) for l in LANGS + [None]})

if __name__ == '__main__':
    main()
