# Thunderbird Archive Viewer

**Version 0.8.2**

A lightweight, read-only Windows viewer for old Thunderbird and MBOX mail archives.

Thunderbird Archive Viewer is intended for browsing archived mail without importing it into a current Thunderbird profile. Source MBOX files are opened read-only; the application builds separate SQLite indexes under `%LOCALAPPDATA%\ThunderbirdArchiveViewer\indexes`.

## Features

- Browse Thunderbird-style folder trees and MBOX files.
- Fast header-only or Full MIME indexing.
- Persistent SQLite indexes stored outside the source archive.
- Message list with sender, recipient, subject and date information.
- HTML and plain-text message display.
- Light and Dark application themes with independent message rendering.
- Attachment list with Open, Save As and Save All.
- Inline previews for JPEG, PNG, GIF, BMP and WebP image attachments.
- Export the exact original message bytes as `.eml`.
- 64-bit-safe indexing progress for multi-gigabyte mailboxes.

## Read-only archive handling

The application does not modify, rename or compact source MBOX files or Thunderbird `.msf` metadata. MBOX data is opened in read-only mode. Generated indexes are kept separately in the user's local application-data directory.

Opening or saving an attachment necessarily extracts its data from the archived message. **Only open attachments you trust**, just as you would with attachments in any email client.

## Running from source

Requires Windows and Python 3.

1. Run `setup.bat` once. This creates `.venv` and installs the required packages.
2. Run `run.bat` to start the application.

`run.bat` does not reinstall packages on every launch.

## Building the Windows application

After running `setup.bat`, run:

```text
build_exe.bat
```

PyInstaller places the build under `dist\ThunderbirdArchiveViewer\`.

## Version 0.8.2

- Corrected the internal application version to 0.8.2.
- Removed the unused experimental `.msf` display-name parser; folder names continue to use the physical MBOX filenames.
- Changed `run.bat` so dependency installation is handled only by `setup.bat`.
- Added a repository `.gitignore` to keep local archives, indexes, environments and build artifacts out of Git.
- Replaced the text README with this GitHub-friendly Markdown documentation.
- Added an explicit attachment safety note.

## Dependencies

- PySide6 6.7 or newer, below 7
- PyInstaller 6 or newer (for local builds)

See `requirements.txt` for the package constraints.

## Privacy

The application processes archives locally. The source code contains no bundled mail archives, user databases or account credentials.

## License

No open-source license has been selected yet. The source is published for inspection, but no additional reuse rights are granted unless a license is added later.
