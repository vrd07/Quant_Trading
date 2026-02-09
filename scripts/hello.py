print("Hello world")
try:
    with open("data/historical/hello.txt", "w") as f:
        f.write("Hello file content in data dir")
    print("File written")
except Exception as e:
    print(f"Error: {e}")
