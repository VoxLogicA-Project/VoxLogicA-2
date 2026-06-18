# Program output

Gallery and CLI programs that write files should place artifacts under:

```text
output/<program-stem>/
```

where `<program-stem>` is the `.imgql` file name without extension (for example `output/nnunet-circle-segmentation/`).

Use built-in program variables in ImgQL:

- `$stem` — file base name (preferred for output directories)
- `$file` / `$filename` — full source file name
- `$dir` — directory containing the source `.imgql` file
- `$cwd` — process working directory when the program is run

Example:

```imgql
export_root = concat("output/", $stem)
```
