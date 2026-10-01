# Viking review feed

A Klaviyo web feed of customer reviews for Viking's abandoned cart emails.

**Feed URL:** https://matesinc.github.io/viking-review-feed/viking_reviews.json
(Klaviyo web feed name: `viking_reviews`, method GET, content type JSON)

The reviews are public on vikingfootwear.com (Lipscore), and the client approved their use in
email. The feed holds no customer names or contact data.

## Shape

```
{ "<profile Language>": { "<first cart category>": { "headline": "...", "reviews": [ {...} ] } } }
```

- Languages: `en`, `no`, `sv`, `da`, `fi`, `de` (Klaviyo profile property `Language`).
- Categories: `kids`, `women`, `men`, `clothing`, `outlet` (first category of the first cart
  line, `event.Items.0.Categories.0`), plus `all`.
- Review fields: `text` (verbatim), `rating`, `stars` (image URL), `product`, `url`, `image`
  (900x900), `date`, `country`.

## Rules

- Lipscore ratings of 4 or 5 with 40 to 220 characters of text; product reviews only; return
  or exchange requests and questions are left out.
- Product photo from the product page; a review whose product page is gone is dropped.
- Per pool: the customer's own language first, English second, never a third language. Up to
  8 reviews, newest first. An empty pool borrows `all`.
- Lipscore's language tag is the language of the review form, not of the text, so English is
  detected from the words.

## Refresh

`.github/workflows/refresh.yml` rebuilds the feed daily from the Klaviyo metric "Lipscore
Rating". It needs a repository secret `KLAVIYO_API_KEY`: a Klaviyo private key with read access
to events only. Without the secret the job does nothing and the last feed stays online.
Run by hand: `KLAVIYO_API_KEY=... python3 build_feed.py --klaviyo viking_reviews.json --cache pages.json`.
`pages.json` caches product pages (photo and audience); delete an entry to re-read a page.
