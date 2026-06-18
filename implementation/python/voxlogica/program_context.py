"""Built-in program path variables for ImgQL reduction."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

PROGRAM_SYSVAR_NAMES: frozenset[str] = frozenset(
    {
        "$cwd",
        "$dir",
        "$file",
        "$filename",
        "$stem",
    }
)


@dataclass(frozen=True)
class ProgramContext:
    """Resolved filesystem metadata for the program being reduced."""

    cwd: str
    dir: str
    file: str
    stem: str

    @classmethod
    def from_source_name(cls, source_name: str) -> ProgramContext:
        """Build context from a CLI path, file path, or synthetic source label."""
        cwd = Path.cwd().resolve()
        label = str(source_name or "<input>").strip() or "<input>"
        source_path = Path(label)

        looks_like_path = (
            source_path.suffix.lower() == ".imgql"
            and (source_path.is_absolute() or "/" in label or "\\" in label)
        )
        if looks_like_path:
            resolved = source_path if source_path.is_absolute() else (cwd / source_path)
            resolved = resolved.resolve()
            return cls(
                cwd=str(cwd),
                dir=str(resolved.parent),
                file=resolved.name,
                stem=resolved.stem,
            )

        fallback_stem = "program" if label in {"<input>", "<repl>"} else Path(label).stem or "program"
        return cls(
            cwd=str(cwd),
            dir=str(cwd),
            file=label,
            stem=fallback_stem,
        )

    def bindings(self) -> dict[str, str]:
        """Return immutable string bindings injected into the reducer environment."""
        return {
            "$cwd": self.cwd,
            "$dir": self.dir,
            "$file": self.file,
            "$filename": self.file,
            "$stem": self.stem,
        }
