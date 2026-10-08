"""montage.py <out.png> <cols> <width> <png>...  contact sheet, for checking pages and shots by eye."""
import sys
from PIL import Image

out, cols, w = sys.argv[1], int(sys.argv[2]), int(sys.argv[3])
ims = []
for f in sys.argv[4:]:
    im = Image.open(f).convert('RGB')
    im.thumbnail((w, w * 2))
    ims.append(im)
h = max(i.height for i in ims)
rows = (len(ims) + cols - 1) // cols
M = Image.new('RGB', (cols * (w + 8), rows * (h + 8)), (90, 90, 90))
for k, im in enumerate(ims):
    M.paste(im, ((k % cols) * (w + 8), (k // cols) * (h + 8)))
M.save(out)
