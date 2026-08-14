# pyright: reportMissingModuleSource=false
import html
import json
import os
import platform
import re
import subprocess
from typing import Any, NamedTuple

WD_WITH_IN_TABLE = 12  # Word constant: wdWithInTable


class FieldSpan(NamedTuple):
    """A Word field inside the current selection.

    doc_start is the position of the field begin character, doc_end is the
    position after the field end character, and result_text is the visible
    result Word displays for the field (e.g. an EndNote citation).
    """

    doc_start: int
    doc_end: int
    result_text: str


class WordIntegration:
    """Abstract base class for Microsoft Word interaction."""

    def ensure_ready(self) -> None:
        raise NotImplementedError

    def ensure_track_changes_enabled(self) -> None:
        raise NotImplementedError

    def get_selection_info(self) -> tuple[str, int, int, str, str]:
        """Returns (text, start_index, end_index, context_before, context_after)"""
        raise NotImplementedError

    def is_selection_in_table(self) -> bool:
        raise NotImplementedError

    def add_comment(self, comment_text: str) -> None:
        raise NotImplementedError

    def delete_range(self, abs_start: int, abs_end: int) -> None:
        raise NotImplementedError

    def insert_at_position(self, abs_pos: int, text: str) -> None:
        raise NotImplementedError

    def replace_range(self, abs_start: int, abs_end: int, text: str) -> None:
        raise NotImplementedError

    def replace_selection_content(self, new_text: str) -> None:
        raise NotImplementedError

    def selection_has_fields(self) -> bool:
        raise NotImplementedError

    def get_selection_field_spans(self) -> list[FieldSpan]:
        """Return the fields in the current selection as FieldSpan items."""
        raise NotImplementedError

    def get_selection_hidden_spans(
        self,
        start: int = 0,
        end: int = 0,
        exclude_spans: list[tuple[int, int]] | None = None,
        max_hidden: int = 0,
    ) -> list[tuple[int, int]]:
        """Return document positions of text hidden from the selection text.

        Tracked deletions still occupy Word's internal character positions but
        are omitted from Range.Text / AppleScript ``content``. Every such span
        must be compensated for when mapping visible-text offsets back to
        absolute document positions.
        """
        raise NotImplementedError

# --- Windows Implementation ---

class WindowsWordIntegration(WordIntegration):
    client: Any # pyright: ignore[reportAny]

    def __init__(self) -> None:
        try:
            import importlib
            self.client = importlib.import_module("win32com.client")
        except ImportError:
            # Mock for non-Windows environments to satisfy linter/runtime
            class MockObject:
                def __getattr__(self, name: str) -> 'MockObject': return MockObject()
                def __call__(self, *args: Any, **kwargs: Any) -> 'MockObject': return MockObject() # pyright: ignore[reportAny]
                def __bool__(self) -> bool: return False
                def __int__(self) -> int: return 0
                def __len__(self) -> int: return 0
                def __add__(self, other: Any) -> int: return 0 # pyright: ignore[reportAny]
                def __sub__(self, other: Any) -> int: return 0 # pyright: ignore[reportAny]
                def __lt__(self, other: Any) -> bool: return False # pyright: ignore[reportAny]
                def __gt__(self, other: Any) -> bool: return False # pyright: ignore[reportAny]
                def __setattr__(self, name: str, value: Any) -> None: pass # pyright: ignore[reportAny]
                def __str__(self) -> str: return ""
            
            class MockClient:
                def GetActiveObject(self, name: str) -> MockObject: return MockObject()
                
            self.client = MockClient()

    def _get_word(self) -> Any: # pyright: ignore[reportAny]
        try:
            import pythoncom
            pythoncom.CoInitialize()
        except Exception:
            pass
        return self.client.GetActiveObject("Word.Application") # pyright: ignore[reportAny, reportUnknownMemberType]

    def ensure_ready(self) -> None:
        try:
            word = self._get_word()
            if not word.Documents.Count:
                raise RuntimeError("No active Word document is open.")
        except Exception as e:
            raise RuntimeError(f"Microsoft Word is not running or no document is open: {e}")

    def ensure_track_changes_enabled(self) -> None:
        try:
            word = self._get_word()
            if word.Documents.Count > 0:
                word.ActiveDocument.TrackRevisions = True
        except Exception as e:
            raise RuntimeError(f"Unable to enable Track Changes in Microsoft Word: {e}")

    def replace_selection_content(self, new_text: str) -> None:
        try:
            word = self._get_word()
            sel = word.Selection
            sel.Text = new_text
        except Exception as e:
            print(f"Error replacing selection content (Windows): {e}")

    def get_selection_info(self) -> tuple[str, int, int, str, str]:
        try:
            word = self._get_word()
            if not word.Documents.Count:
                return "", 0, 0, "", ""
                
            doc = word.ActiveDocument
            sel = word.Selection
            text = sel.Text
            start_pos = sel.Start
            end_pos = sel.End
            
            before_start = max(0, start_pos - 250)
            if before_start < start_pos:
                context_before = doc.Range(before_start, start_pos).Text
            else:
                context_before = ""
                
            doc_end = doc.Content.End
            after_end = min(doc_end, end_pos + 250)
            if end_pos < after_end:
                context_after = doc.Range(end_pos, after_end).Text
            else:
                context_after = ""
                
            return str(text), int(start_pos), int(end_pos), str(context_before), str(context_after)
        except Exception as e:
            print(f"Error getting text (Windows): {e}")
            return "", 0, 0, "", ""

    def is_selection_in_table(self) -> bool:
        try:
            word = self._get_word()
            return bool(word.Selection.Information(WD_WITH_IN_TABLE))
        except Exception:
            return False

    def delete_range(self, abs_start: int, abs_end: int) -> None:
        try:
            word = self._get_word()
            doc = word.ActiveDocument
            doc.Range(abs_start, abs_end).Delete()
        except Exception as e:
            print(f"Error deleting range (Windows): {e}")

    def insert_at_position(self, abs_pos: int, text: str) -> None:
        try:
            word = self._get_word()
            doc = word.ActiveDocument
            doc.Range(abs_pos, abs_pos).InsertAfter(text)
        except Exception as e:
            print(f"Error inserting at position (Windows): {e}")

    def replace_range(self, abs_start: int, abs_end: int, text: str) -> None:
        try:
            word = self._get_word()
            doc = word.ActiveDocument
            doc.Range(abs_start, abs_end).Text = text
        except Exception as e:
            print(f"Error replacing range (Windows): {e}")

    def selection_has_fields(self) -> bool:
        try:
            word = self._get_word()
            sel = word.Selection
            return sel.Fields.Count > 0
        except Exception:
            return False

    def get_selection_field_spans(self) -> list[FieldSpan]:
        try:
            word = self._get_word()
            sel = word.Selection
            spans: list[FieldSpan] = []
            for field in sel.Fields:
                try:
                    spans.append(
                        FieldSpan(
                            int(field.Range.Start),
                            int(field.Range.End),
                            str(field.Result.Text or ""),
                        )
                    )
                except Exception:
                    continue
            return spans
        except Exception as e:
            print(f"Error getting field spans (Windows): {e}")
            return []

    def get_selection_hidden_spans(
        self,
        start: int = 0,
        end: int = 0,
        exclude_spans: list[tuple[int, int]] | None = None,
        max_hidden: int = 0,
    ) -> list[tuple[int, int]]:
        """Tracked deletions (and moved-from text) inside the selection."""
        try:
            word = self._get_word()
            if not word.Documents.Count:
                return []
            doc = word.ActiveDocument
            sel = word.Selection
            if int(sel.End) <= int(sel.Start):
                return []

            spans: list[tuple[int, int]] = []
            # wdRevisionDelete = 2, wdRevisionMovedFrom = 17,
            # wdRevisionCellDeletion = 20. These hide text from Range.Text.
            hidden_types = (2, 17, 20)
            for rev in doc.Range(int(sel.Start), int(sel.End)).Revisions:
                try:
                    if int(rev.Type) in hidden_types:
                        spans.append(
                            (int(rev.Range.Start), int(rev.Range.End))
                        )
                except Exception:
                    continue
            return spans
        except Exception as e:
            print(f"Error getting hidden spans (Windows): {e}")
            return []

    def add_comment(self, comment_text: str) -> None:
        if not comment_text or not comment_text.strip():
            print("Skipping empty comment insertion.")
            return
        try:
            word = self._get_word()
            doc = word.ActiveDocument
            sel = word.Selection
            rng = doc.Range(sel.Start, sel.End)
            doc.Comments.Add(rng, comment_text)
        except Exception as e:
            print(f"Error adding comment (Windows): {e}")


# --- macOS Implementation ---

class MacOSWordIntegration(WordIntegration):
    
    def _run_applescript(self, script: str, *args: str) -> str:
        """
        Executes an AppleScript using `osascript`.
        Uses stdin for the script content to avoid command line length limits.
        Ensures binary mode execution to preserve newlines correctly.
        """
        command = ["osascript", "-"]
        command.extend(args)
        
        try:
            completed = subprocess.run(
                command,
                input=script.encode('utf-8'),
                check=True,
                capture_output=True,
                text=False, # Binary mode
            )
            
            # Decode and handle trailing newline from osascript
            output = completed.stdout.decode('utf-8')
            output = output.removesuffix("\n")
            return output
        except subprocess.CalledProcessError as e:
            # Re-raise with stderr context decoded
            stdout_str = e.stdout.decode('utf-8') if e.stdout else ""
            stderr_str = e.stderr.decode('utf-8') if e.stderr else ""
            raise subprocess.CalledProcessError(e.returncode, e.cmd, output=stdout_str, stderr=stderr_str)

    def ensure_ready(self) -> None:
        script = """
        tell application "Microsoft Word"
            if not running then error "Microsoft Word is not running."
            if not (exists active document) then error "No active Word document is open."
        end tell
        """
        self._run_applescript(script)

    def ensure_track_changes_enabled(self) -> None:
        scripts = [
            """
            tell application "Microsoft Word"
                set track revisions of active document to true
            end tell
            """,
            """
            tell application "Microsoft Word"
                set track changes of active document to true
            end tell
            """
        ]
        last_error = None
        for script in scripts:
            try:
                self._run_applescript(script)
                return
            except Exception as exc:
                last_error = exc
        if last_error:
            raise RuntimeError("Unable to enable Track Changes in Microsoft Word.") from last_error

    def get_selection_info(self) -> tuple[str, int, int, str, str]:
        script = """
        tell application "Microsoft Word"
            if not (exists active document) then error "No active Word document is open."
            
            -- Get selection
            set mySelection to selection
            set myRange to text object of mySelection
            
            -- Get selection content and start position (0-based).
            set startPos to start of content of myRange
            set myContent to content of myRange

            -- CRITICAL: Use the real range end. Word's internal character
            -- positions include hidden field code characters (e.g. EndNote
            -- citations), so startPos + (length of myContent) is too small
            -- whenever the selection contains a field.
            set endPos to end of content of myRange
            
            -- Context Before (approx 30 words -> ~250 chars)
            set contextBefore to ""
            if startPos > 0 then
                set beforeStart to startPos - 250
                if beforeStart < 0 then set beforeStart to 0
                set rangeBefore to create range active document start beforeStart end startPos
                set contextBefore to content of rangeBefore
            end if
            
            -- Context After
            set docRange to text object of active document
            set docEnd to end of content of docRange
            
            set contextAfter to ""
            if endPos < docEnd then
                set afterEnd to endPos + 250
                if afterEnd > docEnd then set afterEnd to docEnd
                set rangeAfter to create range active document start endPos end afterEnd
                set contextAfter to content of rangeAfter
            end if
            
            -- Use a unique separator to avoid issues with pipe in text
            return (startPos as string) & "###PROOF_SEP###" & myContent & "###PROOF_SEP###" & (endPos as string) & "###PROOF_SEP###" & contextBefore & "###PROOF_SEP###" & contextAfter
        end tell
        """
        try:
            result = self._run_applescript(script)
            if "###PROOF_SEP###" in result:
                parts = result.split("###PROOF_SEP###")
                if len(parts) >= 5:
                    return parts[1], int(parts[0]), int(parts[2]), parts[3], parts[4]
                if len(parts) >= 4:
                    return parts[1], int(parts[0]), 0, parts[2], parts[3]
                if len(parts) >= 2:
                    return parts[1], int(parts[0]), 0, "", ""
        except Exception as e:
            print(f"Error getting text with context: {e}")
            
        return "", 0, 0, "", ""

    def is_selection_in_table(self) -> bool:
        script = """
        try
            tell application "Microsoft Word"
                if not (exists active document) then return "false"
                try
                    set myRange to text object of selection
                    if (count of tables of myRange) > 0 then
                        return "true"
                    end if
                end try
                return "false"
            end tell
        on error errMsg
            return "false"
        end try
        """
        try:
            res = self._run_applescript(script)
            return res.strip() == "true"
        except Exception as e:
            print(f"Error checking table status: {e}")
            return False
            
    def delete_range(self, abs_start: int, abs_end: int) -> None:
        script = """
        on run argv
            set absStart to (item 1 of argv) as integer
            set absEnd to (item 2 of argv) as integer
            try
                tell application "Microsoft Word"
                    set theRange to create range active document start absStart end absEnd
                    delete theRange
                end tell
            on error errMsg number errNum
                if errNum is -1728 then
                    return "ERROR_OBJECT_NOT_FOUND"
                else if errNum is -10006 then
                    return "ERROR_WRITE_DENIED"
                else
                    error errMsg number errNum
                end if
            end try
        end run
        """
        res = self._run_applescript(script, str(abs_start), str(abs_end))
        self._handle_script_error(res, f"delete range {abs_start}-{abs_end}")

    def insert_at_position(self, abs_pos: int, text: str) -> None:
        script = """
        on run argv
            set absPos to (item 1 of argv) as integer
            set newText to item 2 of argv
            try
                tell application "Microsoft Word"
                    set docEnd to (end of content of text object of active document) - 1
                    if absPos >= docEnd then
                        set theRange to create range active document start docEnd end docEnd
                        set content of theRange to (content of theRange) & newText
                    else
                        set theRange to create range active document start absPos end (absPos + 1)
                        set existingChar to content of theRange
                        if existingChar is missing value then set existingChar to ""
                        set content of theRange to newText & existingChar
                    end if
                end tell
            on error errMsg number errNum
                if errNum is -1728 then
                    return "ERROR_OBJECT_NOT_FOUND"
                else if errNum is -10006 then
                    return "ERROR_WRITE_DENIED"
                else
                    error errMsg number errNum
                end if
            end try
        end run
        """
        res = self._run_applescript(script, str(abs_pos), text)
        self._handle_script_error(res, f"insert at {abs_pos}")

    def replace_range(self, abs_start: int, abs_end: int, text: str) -> None:
        script = """
        on run argv
            set absStart to (item 1 of argv) as integer
            set absEnd to (item 2 of argv) as integer
            set newText to item 3 of argv
            try
                tell application "Microsoft Word"
                    set theRange to create range active document start absStart end absEnd
                    set content of theRange to newText
                end tell
            on error errMsg number errNum
                if errNum is -1728 then
                    return "ERROR_OBJECT_NOT_FOUND"
                else if errNum is -10006 then
                    return "ERROR_WRITE_DENIED"
                else
                    error errMsg number errNum
                end if
            end try
        end run
        """
        res = self._run_applescript(script, str(abs_start), str(abs_end), text)
        self._handle_script_error(res, f"replace range {abs_start}-{abs_end}")

    def replace_selection_content(self, new_text: str) -> None:
        safe_text = new_text.replace("\r", "\n")
        import tempfile
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".txt", delete=False, encoding="utf-8"
        ) as tmp:
            tmp.write(safe_text)
            tmp_path = tmp.name
        try:
            script = """
            on run argv
                set filePath to item 1 of argv
                try
                    set newContent to read (filePath as POSIX file)
                on error
                    return "ERROR_READ_FAILED"
                end try
                try
                    tell application "Microsoft Word"
                        set content of text object of selection to newContent
                    end tell
                    return "OK"
                on error errMsg number errNum
                    return "ERROR:" & errMsg & "(" & (errNum as string) & ")"
                end try
            end run
            """
            res = self._run_applescript(script, tmp_path)
            self._handle_script_error(res, "replace selection content")
        finally:
            try:
                os.unlink(tmp_path)
            except Exception:
                pass

    def selection_has_fields(self) -> bool:
        script = """
        on run
            try
                tell application "Microsoft Word"
                    set fieldCount to (count of fields of text object of selection)
                    return (fieldCount as string)
                end tell
            on error
                return "0"
            end try
        end run
        """
        try:
            res = self._run_applescript(script)
            return int(res.strip()) > 0
        except Exception:
            return False

    def get_selection_field_spans(self) -> list[FieldSpan]:
        """Return fields in the current selection on macOS.

        Word's AppleScript dictionary exposes the field code range and code
        text, but not the result range directly. The visible result normally
        matches the citation text embedded in the field code (EndNote's
        DisplayText or Zotero's formatted citation); a bounded character scan
        is used as a fallback for other field types.
        """
        list_script = """
        on run
            try
                tell application "Microsoft Word"
                    if not (exists active document) then return ""
                    set fs to fields of text object of selection
                    set out to ""
                    repeat with i from 1 to (count of fs)
                        set f to item i of fs
                        set codeStart to start of content of field code of f
                        set codeEnd to end of content of field code of f
                        set codeText to ""
                        try
                            set codeText to content of field code of f
                        end try
                        set out to out & (codeStart as string) & "###FIELD_SPAN###" & (codeEnd as string) & "###FIELD_SPAN###" & codeText & "###FIELD_END###"
                    end repeat
                    return out
                end tell
            on error
                return ""
            end try
        end run
        """
        try:
            raw = self._run_applescript(list_script)
        except Exception as e:
            print(f"Error listing Word fields (macOS): {e}")
            return []

        spans: list[FieldSpan] = []
        for chunk in raw.split("###FIELD_END###"):
            if "###FIELD_SPAN###" not in chunk:
                continue
            parts = chunk.split("###FIELD_SPAN###")
            if len(parts) < 3:
                continue
            try:
                code_start = int(parts[0].strip())
                code_end = int(parts[1].strip())
            except ValueError:
                continue
            code_text = parts[2]
            result_start = code_end + 1

            expected = self._field_result_from_code(code_text)
            if expected is not None:
                result_end = result_start + len(expected)
                actual = self._mac_read_range_text(result_start, result_end)
                if actual == expected:
                    spans.append(
                        FieldSpan(
                            max(0, code_start - 1),
                            result_end + 1,
                            actual,
                        )
                    )
                    continue

            result_text, result_end = self._mac_scan_field_result(result_start)
            spans.append(
                FieldSpan(
                    max(0, code_start - 1),
                    result_end + 1,
                    result_text,
                )
            )
        return spans

    def get_selection_hidden_spans(
        self,
        start: int = 0,
        end: int = 0,
        exclude_spans: list[tuple[int, int]] | None = None,
        max_hidden: int = 0,
    ) -> list[tuple[int, int]]:
        """Return tracked-deletion spans inside the current selection.

        Word does not expose a range-scoped revision collection through
        AppleScript, but it does omit deleted characters from ``content``
        while still counting them in document positions. A one-pass character
        scan over the selection locates those missing positions without ever
        touching revisions elsewhere in a long document. Field spans are
        excluded because field-code characters are hidden for a different
        reason and are already mapped separately.
        """
        exclude_spans = exclude_spans or []
        exclude_args: list[str] = []
        for span_start, span_end in exclude_spans:
            exclude_args.extend((str(span_start), str(span_end)))

        script = """
        on run argv
            set aStart to (item 1 of argv) as integer
            set aEnd to (item 2 of argv) as integer
            set maxHidden to (item 3 of argv) as integer
            set out to ""
            set argCount to count of argv
            set foundCount to 0
            try
                tell application "Microsoft Word"
                    if not (exists active document) then return ""
                    set d to active document
                    set runStart to -1
                    repeat with p from aStart to (aEnd - 1)
                        set excluded to false
                        repeat with i from 4 to argCount by 2
                            if p >= (item i of argv as integer) and p < (item (i + 1) of argv as integer) then
                                set excluded to true
                                exit repeat
                            end if
                        end repeat
                        if not excluded then
                            set r to create range d start p end (p + 1)
                            set c to content of r
                            if c is missing value then
                                if runStart is -1 then set runStart to p
                                set foundCount to foundCount + 1
                            else
                                if runStart is not -1 then
                                    set out to out & (runStart as string) & "###HSPAN###" & (p as string) & "###HSEND###"
                                    set runStart to -1
                                end if
                            end if
                        else
                            if runStart is not -1 then
                                set out to out & (runStart as string) & "###HSPAN###" & (p as string) & "###HSEND###"
                                set runStart to -1
                            end if
                        end if
                        if maxHidden > 0 and foundCount >= maxHidden then
                            if runStart is not -1 then
                                set out to out & (runStart as string) & "###HSPAN###" & ((p + 1) as string) & "###HSEND###"
                                set runStart to -1
                            end if
                            exit repeat
                        end if
                    end repeat
                    if runStart is not -1 then
                        set out to out & (runStart as string) & "###HSPAN###" & (aEnd as string) & "###HSEND###"
                    end if
                    return out
                end tell
            on error
                return ""
            end try
        end run
        """
        args = [str(start), str(end), str(max_hidden)] + exclude_args
        try:
            raw = self._run_applescript(script, *args)
        except Exception as e:
            print(f"Error scanning Word selection (macOS): {e}")
            return []

        spans: list[tuple[int, int]] = []
        for chunk in raw.split("###HSEND###"):
            if "###HSPAN###" not in chunk:
                continue
            parts = chunk.split("###HSPAN###")
            if len(parts) < 2:
                continue
            try:
                spans.append((int(parts[0].strip()), int(parts[1].strip())))
            except ValueError:
                continue
        return spans

    def _mac_read_range_text(self, start: int, end: int) -> str:
        script = """
        on run argv
            set aStart to (item 1 of argv) as integer
            set aEnd to (item 2 of argv) as integer
            try
                tell application "Microsoft Word"
                    set r to create range active document start aStart end aEnd
                    return content of r
                end tell
            on error
                return ""
            end try
        end run
        """
        try:
            return self._run_applescript(script, str(start), str(end))
        except Exception:
            return ""

    def _mac_scan_field_result(self, result_start: int) -> tuple[str, int]:
        script = """
        on run argv
            set resultStart to (item 1 of argv) as integer
            try
                tell application "Microsoft Word"
                    set out to ""
                    set p to resultStart
                    repeat 500 times
                        set r to create range active document start p end (p + 1)
                        set c to ""
                        try
                            set c to content of r
                        on error
                            set c to missing value
                        end try
                        if c is missing value or c is "" then
                            return "###SCAN###" & out & "###SCAN###" & (p as string)
                        end if
                        set out to out & c
                        set p to p + 1
                    end repeat
                    return "###SCAN###" & out & "###SCAN###" & (p as string)
                end tell
            on error
                return ""
            end try
        end run
        """
        try:
            res = self._run_applescript(script, str(result_start))
        except Exception:
            return "", result_start
        if "###SCAN###" in res:
            parts = res.split("###SCAN###")
            if len(parts) >= 3:
                try:
                    return parts[1], int(parts[2])
                except ValueError:
                    pass
        return "", result_start

    @staticmethod
    def _field_result_from_code(code_text: str) -> str | None:
        """Extract the visible result text from EndNote/Zotero field code."""
        if not code_text:
            return None
        match = re.search(r"<DisplayText>(.*?)</DisplayText>", code_text, re.DOTALL)
        if match:
            return html.unescape(match.group(1))
        for key in ("formattedCitation", "plainCitation"):
            match = re.search(
                re.escape(key) + r'"\s*:\s*("(?:\\.|[^"\\])*")',
                code_text,
                re.DOTALL,
            )
            if match:
                try:
                    return json.loads(match.group(1))
                except Exception:
                    continue
        return None

    def add_comment(self, comment_text: str) -> None:
        import subprocess as sp

        if not comment_text or not comment_text.strip():
            print("Skipping empty comment insertion.")
            return

        try:
            saved_clipboard = None
            try:
                result = sp.run(["pbpaste"], capture_output=True, text=True, check=False)
                if result.returncode == 0:
                    saved_clipboard = result.stdout
            except Exception:
                pass

            sp.run(["pbcopy"], input=comment_text.encode("utf-8"), check=True)

            try:
                self._trigger_comment_and_paste()
            finally:
                if saved_clipboard is not None:
                    try:
                        sp.run(["pbcopy"], input=saved_clipboard.encode("utf-8"), check=True)
                    except Exception:
                        pass

        except Exception as e:
            print(f"Comment insertion failed: {e}")
            raise

    def _trigger_comment_and_paste(self) -> None:
        last_error = "unknown"
        methods = [
            ('menu_insert', '''
                tell application "Microsoft Word" to activate
                delay 0.3
                tell application "System Events"
                    tell process "Microsoft Word"
                        set frontmost to true
                        delay 0.15
                    end tell
                end tell
                tell application "System Events"
                    tell process "Microsoft Word"
                        click menu item "Comment" of menu "Insert" of menu bar 1
                    end tell
                end tell
            '''),
            ('cmd_opt_a', '''
                tell application "Microsoft Word" to activate
                delay 0.3
                tell application "System Events"
                    tell process "Microsoft Word"
                        set frontmost to true
                        delay 0.15
                        keystroke "a" using {command down, option down}
                    end tell
                end tell
            '''),
            ('cmd_shift_a', '''
                tell application "Microsoft Word" to activate
                delay 0.3
                tell application "System Events"
                    tell process "Microsoft Word"
                        set frontmost to true
                        delay 0.15
                        keystroke "a" using {command down, shift down}
                    end tell
                end tell
            '''),
        ]

        triggered = False
        for method_name, method_script in methods:
            try:
                result = self._run_applescript(method_script)
                if result and "ERROR" in result:
                    last_error = f"{method_name}: {result}"
                    print(f"  Comment method '{method_name}' failed: {result}")
                    continue
                triggered = True
                print(f"  Comment triggered via '{method_name}'")
                break
            except Exception as ex:
                last_error = f"{method_name}: {ex}"
                print(f"  Comment method '{method_name}' exception: {ex}")
                continue

        if not triggered:
            raise RuntimeError(f"No comment trigger method worked. Last error: {last_error}")

        paste_script = '''
            delay 0.5
            tell application "System Events"
                tell process "Microsoft Word"
                    keystroke "v" using {command down}
                    delay 0.3
                    keystroke return using {command down}
                end tell
            end tell
            return "OK"
        '''
        self._run_applescript(paste_script)

    def _handle_script_error(self, res: str, context: str):
        if res:
            res = res.strip()
            if res == "ERROR_OBJECT_NOT_FOUND":
                print(f"  ! Warning: Could not {context} (Object not found). Skipping.")
            elif res == "ERROR_WRITE_DENIED":
                print(f"  ! Warning: Write denied for {context}. Skipping.")


# --- Factory ---

def get_word_integration() -> WordIntegration:
    if platform.system() == "Windows":
        return WindowsWordIntegration()
    else:
        return MacOSWordIntegration()
