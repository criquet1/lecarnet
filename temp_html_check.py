from html.parser import HTMLParser
import re, pathlib
path = pathlib.Path('facture/templates/facture/facture.html')
text = path.read_text(encoding='utf-8')
clean = re.sub(r'\{\%.*?\%\}|\{\{.*?\}\}', '', text, flags=re.S)
class MyParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.stack = []
        self.errors = []
    def handle_starttag(self, tag, attrs):
        if tag in ['area','base','br','col','embed','hr','img','input','link','meta','param','source','track','wbr']:
            return
        self.stack.append(tag)
    def handle_endtag(self, tag):
        if not self.stack:
            self.errors.append(('extra_close', tag))
            return
        last = self.stack.pop()
        if last != tag:
            self.errors.append(('mismatch', last, tag))
parser = MyParser()
parser.feed(clean)
print('errors', parser.errors)
print('unclosed', parser.stack[:20])
