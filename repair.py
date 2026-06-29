# fix_paths.py — run from the TimeTable folder
import os

pages_dir = os.path.join("app", "other_colleges_frontend", "frontend", "pages")

for filename in os.listdir(pages_dir):
    if filename.endswith(".html"):
        filepath = os.path.join(pages_dir, filename)
        with open(filepath, "r", encoding="utf-8") as f:
            content = f.read()

        content = content.replace("../assets/css/styles.css", "/college-assets/css/styles.css")
        content = content.replace("../assets/img/logo.png", "/college-assets/img/logo.png")

        with open(filepath, "w", encoding="utf-8") as f:
            f.write(content)

        print(f"Fixed: {filename}")

print("Done!")