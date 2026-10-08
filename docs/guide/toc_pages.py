"""toc_pages.py <pdf>  prints the page number each contents link leads to, "3,5,...".
The contents is the second page; Chromium writes its links as GoTo actions."""
import sys
from pypdf import PdfReader

r = PdfReader(sys.argv[1])
index = {p.indirect_reference.idnum: i for i, p in enumerate(r.pages)}
named = r.named_destinations
out = []
for a in r.pages[1].get("/Annots") or []:
    a = a.get_object()
    if a.get("/Subtype") != "/Link":
        continue
    d = a.get("/Dest")
    if d is None and "/A" in a:
        d = a["/A"].get("/D")
    if isinstance(d, (str, bytes)) or hasattr(d, "decode"):
        d = named[str(d)].page if str(d) in named else None
        page = index.get(d.idnum) if d is not None else None
    else:
        page = index.get(d[0].idnum)
    top = float(a["/Rect"][3])
    out.append((-top, page + 1 if page is not None else ""))
print(",".join(str(p) for _, p in sorted(out)))
