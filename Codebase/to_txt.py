import os

def parse_files_to_txt(folder_path, output_file="parsed_files.txt"):
    # Folders to skip
    skip_folders = {"venv", "__pycache__", ".git", ".idea", ".vscode", "node_modules"}
    # Common text-based file extensions (you can add more if needed)
    text_extensions = {".txt", ".py", ".csv", ".json", ".md", ".html", ".css", ".js"}

    try:
        with open(output_file, "w", encoding="utf-8") as out_file:
            for root, dirs, files in os.walk(folder_path):
                # Skip unwanted folders
                dirs[:] = [d for d in dirs if d not in skip_folders and not d.startswith(".")]

                for file in files:
                    file_path = os.path.join(root, file)
                    file_ext = os.path.splitext(file)[1].lower()

                    # Skip non-text files unless needed
                    if file_ext not in text_extensions:
                        continue

                    try:
                        with open(file_path, "r", encoding="utf-8") as f:
                            content = f.read()
                    except UnicodeDecodeError:
                        content = "[BINARY FILE - CONTENT NOT READ]"
                    except Exception as e:
                        content = f"[ERROR READING FILE: {e}]"

                    # Write filename and content to output
                    out_file.write(f"{file}:\n{content}\n\n")

        print(f"✅ Parsing complete! Output saved to: {output_file}")

    except Exception as e:
        print(f"❌ Error: {e}")


# Example usage:
folder_path = "Refassist Codebase"  # 🔹 Change this to your folder path
parse_files_to_txt(folder_path)
