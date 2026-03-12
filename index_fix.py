import sys

with open(sys.argv[1]) as f:
    text = f.read()

# Let's extract the first valid HTML structure.
# The original file is about 680 lines. Let's look for the first </html>
end_idx = text.find('</html>') + 7
clean_html = text[:end_idx]

with open(sys.argv[1], 'w') as f:
    f.write(clean_html)

print(f"Truncated to {len(clean_html)} chars, up to first </html>")
