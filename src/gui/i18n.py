"""UI language for the new window: one language at a time (German default).

``tr(key, **values)`` looks a text up in :data:`STRINGS` for the active
language and fills ``{placeholders}``. Number, size and time formatting
follow the language too (decimal comma in German).
"""

from __future__ import annotations

from core.serial import serial_text

LANGUAGES = ("de", "en")

_language = "de"

# key -> (German, English)
STRINGS: dict[str, tuple[str, str]] = {
    # Rail and menus
    "nav.delete": ("Löschen", "Delete"),
    "nav.certificates": ("Zertifikate", "Certificates"),
    "nav.keys": ("Schlüssel", "Keys"),
    "nav.options": ("Optionen", "Options"),
    "rail.main_menu": ("Hauptmenü", "Main menu"),
    "rail.language_button": ("EN", "DE"),
    "rail.language_tip": ("Switch to English", "Auf Deutsch umschalten"),
    "rail.theme_to_dark": ("Dunkles Design einschalten", "Switch to dark theme"),
    "rail.theme_to_light": ("Helles Design einschalten", "Switch to light theme"),
    "rail.theme_hint": ("Rechtsklick: wie Windows", "Right-click: follow Windows"),
    "menu.theme_system": ("Design wie Windows", "Theme like Windows"),
    "menu.theme_light": ("Helles Design", "Light theme"),
    "menu.theme_dark": ("Dunkles Design", "Dark theme"),
    # Selection
    "select.no_disk_title": ("Kein Datenträger gewählt", "No disk selected"),
    "select.no_disk_hint": (
        "USB-Stick anschließen oder unten einen Datenträger wählen.",
        "Plug in a USB stick or choose a disk below.",
    ),
    "select.choose_other": ("Anderen wählen", "Choose another"),
    "select.other_method": ("Andere Methode …", "Other method …"),
    "select.delete_button": ("Jetzt sicher löschen", "Securely delete now"),
    "select.estimate": (
        "ca. {time} · du bestätigst danach noch einmal mit LÖSCHEN",
        "about {time} · you confirm once more by typing DELETE",
    ),
    "select.estimate_none": (
        "Wähle einen Datenträger, um zu beginnen.",
        "Choose a disk to begin.",
    ),
    "select.others": ("Weitere Datenträger", "Other disks"),
    "select.refresh": ("Aktualisieren", "Refresh"),
    "select.scanning": ("Suche Datenträger …", "Looking for disks …"),
    "select.no_others": ("Keine weiteren Datenträger gefunden.", "No other disks found."),
    "select.allow_internal": ("Interne Festplatten erlauben", "Allow internal disks"),
    "select.allow_internal_hint": (
        "Die Windows-Systemplatte bleibt immer gesperrt.",
        "The Windows system disk always stays locked.",
    ),
    "select.internal_warning": (
        "Interne Festplatte: Prüfe Modell und Seriennummer, bevor du löschst.",
        "Internal disk: check the model and serial number before you wipe it.",
    ),
    "select.bitlocker_warning": (
        "BitLocker-verschlüsselt: alle Daten werden unwiderruflich gelöscht.",
        "BitLocker-encrypted: all data will be irrevocably deleted.",
    ),
    "tile.method": ("Methode", "Method"),
    "tile.verify": ("Prüfung", "Verification"),
    "tile.class": ("Einordnung", "Classification"),
    "method.zero": ("1× Nullen", "1× zeros"),
    "method.standard": ("3× Zufall", "3× random"),
    "method.bsi": ("BSI-VSITR · 7×", "BSI-VSITR · 7×"),
    "method.custom": ("{passes}× {pattern}", "{passes}× {pattern}"),
    "pattern.zero": ("Nullen", "zeros"),
    "pattern.ones": ("Einsen (0xFF)", "ones (0xFF)"),
    "pattern.random": ("Zufall", "random"),
    "note.recommended": ("Empfohlen", "Recommended"),
    "note.multi_pass": ("nur wenn vorgeschrieben", "only if required"),
    "note.custom": ("Benutzerdefiniert", "Custom"),
    "verify.full": ("Jeder Sektor", "Every sector"),
    "verify.full_note": ("verdoppelt die Dauer", "doubles the time"),
    "verify.sample": ("Stichprobe", "Sample"),
    "verify.sample_note": ("100 Sektoren, schneller", "100 sectors, faster"),
    "class.value": ("NIST {category}", "NIST {category}"),
    "class.note": ("DIN 66399 · SK {sk}", "DIN 66399 · class {sk}"),
    "time.under_minute": ("unter 1 Minute", "under 1 minute"),
    "time.one_minute": ("1 Minute", "1 minute"),
    "time.minutes": ("{n} Minuten", "{n} minutes"),
    "time.hours": ("{h} Std. {m} Min.", "{h} h {m} min"),
    "dur.seconds": ("{s} Sek.", "{s} s"),
    "dur.minutes": ("{m} Min. {s} Sek.", "{m} min {s} s"),
    "dur.hours": ("{h} Std. {m} Min.", "{h} h {m} min"),
    "chip.system": ("Systemplatte · gesperrt", "System disk · locked"),
    "chip.internal_locked": ("Intern · gesperrt", "Internal · locked"),
    "chip.internal": ("Intern", "Internal"),
    "chip.bitlocker": ("BitLocker", "BitLocker"),
    "chip.in_use": ("In Benutzung", "In use"),
    "chip.demo": ("Demo", "Demo"),
    "type.usb_stick": ("USB-Stick", "USB stick"),
    "type.usb_hdd": ("USB-Festplatte", "USB hard disk"),
    "type.usb_ssd": ("USB-SSD", "USB SSD"),
    "type.sata_hdd": ("SATA-HDD", "SATA HDD"),
    "type.sata_ssd": ("SATA-SSD", "SATA SSD"),
    "type.nvme": ("NVMe-SSD", "NVMe SSD"),
    "type.sd": ("SD-Karte", "SD card"),
    "type.demo": ("Demo-Datenträger", "Demo disk"),
    "type.disk": ("{connection}-Datenträger", "{connection} disk"),
    "serial": ("SN {serial}", "SN {serial}"),
    "cert_preview.eyebrow": ("Vorschau Löschzertifikat", "Certificate preview"),
    "cert_preview.title": ("Nr. {n} · {model}", "No. {n} · {model}"),
    "cert_preview.qr": ("QR-Code", "QR code"),
    "cert_preview.text": (
        "Prüfer scannen den Code und sehen sofort, ob das Zertifikat echt ist.",
        "Auditors scan the code and see at once whether the certificate is genuine.",
    ),
    "cert.key": ("Schlüssel {fingerprint}", "Key {fingerprint}"),
    "cert.key_unreadable": ("Schlüssel nicht lesbar", "Key not readable"),
    "history.title": ("Verlauf", "History"),
    "history.count_one": ("1 Löschung", "1 wipe"),
    "history.count": ("{n} Löschungen", "{n} wipes"),
    "history.empty": ("Noch keine Löschungen", "No wipes yet"),
    "history.chain_ok": ("Kette unverändert", "Chain unchanged"),
    "history.chain_broken": ("Kette verändert ab Eintrag {n}", "Chain altered from entry {n}"),
    "history.unreadable": ("Verlauf nicht lesbar", "History not readable"),
    # Options dialog
    "options.title": ("Löschvorgang anpassen", "Adjust this wipe"),
    "options.method": ("Methode", "Method"),
    "options.zero": ("Nullen, 1 Durchgang", "Zeros, 1 pass"),
    "options.standard": ("Zufall, 3 Durchgänge", "Random, 3 passes"),
    "options.bsi": ("BSI-VSITR, 7 Durchgänge", "BSI-VSITR, 7 passes"),
    "options.custom": ("Benutzerdefiniert", "Custom"),
    "options.note_recommended": (
        "Empfohlen — ein Durchgang genügt für heutige Datenträger.",
        "Recommended — one pass is enough for today's media.",
    ),
    "options.note_custom": (
        "Eigene Anzahl Durchgänge und eigenes Muster.",
        "Your own number of passes and pattern.",
    ),
    "options.passes": ("Durchgänge", "Passes"),
    "options.pattern": ("Muster", "Pattern"),
    "options.class": ("Schutzklasse", "Protection class"),
    "options.class_1": ("1 — normaler Schutzbedarf", "1 — normal protection needs"),
    "options.class_2": ("2 — hoher Schutzbedarf", "2 — high protection needs"),
    "options.class_3": ("3 — sehr hoher Schutzbedarf", "3 — very high protection needs"),
    "options.operator": ("Bediener", "Operator"),
    "options.operator_placeholder": ("Name für das Zertifikat", "Name for the certificate"),
    "options.verify": ("Prüfung", "Verification"),
    "options.verify_full": ("Vollständig — jeder Sektor (Standard)", "Full — every sector (default)"),
    "options.verify_sample": ("Stichprobe — 100 Sektoren, schneller", "Sample — 100 sectors, faster"),
    "options.format": ("Formatieren", "Format"),
    "options.format_check": ("Danach formatieren", "Format afterwards"),
    "options.fs": ("Dateisystem", "File system"),
    "options.fs_exfat": ("exFAT (empfohlen)", "exFAT (recommended)"),
    "options.fs_fat32": ("FAT32 (bis 32 GB)", "FAT32 (up to 32 GB)"),
    "options.fs_ntfs": ("NTFS (nur Windows)", "NTFS (Windows only)"),
    "options.label": ("Bezeichnung", "Label"),
    "options.apply": ("Übernehmen", "Apply"),
    "common.cancel": ("Abbrechen", "Cancel"),
    "common.yes": ("Ja", "Yes"),
    "common.no": ("Nein", "No"),
    "common.ok": ("OK", "OK"),
    # Confirmation
    "confirm.window_title": ("Löschen bestätigen", "Confirm wipe"),
    "confirm.title": ("Alle Daten werden endgültig gelöscht", "All data will be destroyed for good"),
    "confirm.text": (
        "Danach lässt sich nichts davon wiederherstellen:",
        "None of it can be recovered afterwards:",
    ),
    "confirm.no_letter": ("ohne Laufwerksbuchstaben", "no drive letter"),
    "confirm.prompt": ("Zum Bestätigen LÖSCHEN eintippen:", "Type DELETE to confirm:"),
    "confirm.placeholder": ("LÖSCHEN", "DELETE"),
    "confirm.button": ("Endgültig löschen", "Delete for good"),
    # Running
    "running.title": ("{target} wird gelöscht", "Wiping {target}"),
    "step.write": ("Überschreiben", "Overwrite"),
    "step.verify": ("Prüfen", "Verify"),
    "step.format": ("Formatieren", "Format"),
    "step.cert": ("Zertifikat", "Certificate"),
    "tile.speed": ("Tempo", "Speed"),
    "tile.eta": ("Restzeit", "Time left"),
    "tile.bad": ("Fehlerhafte Sektoren", "Bad sectors"),
    "tile.bad_pending": ("steht im Ergebnis", "shown in the result"),
    "eta.value": ("~ {time}", "~ {time}"),
    "eta.calculating": ("wird berechnet", "calculating"),
    "running.hint_write": ("Jeder Sektor wird überschrieben.", "Every sector is being overwritten."),
    "running.hint_verify_full": (
        "Prüfung liest jeden Sektor zurück und vergleicht ihn mit dem erwarteten Muster.",
        "Verification reads every sector back and compares it with the expected pattern.",
    ),
    "running.hint_verify_sample": (
        "Stichprobe: 100 zufällige Sektoren werden zurückgelesen.",
        "Sample: 100 random sectors are read back.",
    ),
    "running.hint_format": ("Ein neues Dateisystem wird angelegt.", "A new file system is being created."),
    "running.hint_cert": (
        "Das Zertifikat wird erstellt und signiert.",
        "The certificate is being created and signed.",
    ),
    "running.cancel": ("Abbrechen", "Cancel"),
    "running.cancel_title": ("Löschen abbrechen?", "Cancel the wipe?"),
    "running.cancel_text": (
        "Der Datenträger bleibt dann teilweise überschrieben. Wirklich abbrechen?",
        "The disk is then left partly overwritten. Cancel anyway?",
    ),
    "running.stall": (
        "Seit {s} Sekunden kein Fortschritt. Der USB-Controller hängt vielleicht: "
        "abbrechen, Datenträger neu einstecken, erneut starten.",
        "No progress for {s} seconds. The USB controller may have hung: "
        "cancel, replug the drive, start again.",
    ),
    "protocol.title": ("Protokoll", "Log"),
    "log.pass_started": ("Durchgang {i}/{n} gestartet", "Pass {i}/{n} started"),
    "log.pass_done": ("Durchgang {i}/{n} fertig · {size}", "Pass {i}/{n} done · {size}"),
    "log.verify_full": ("Prüfung gestartet · alle Sektoren", "Verification started · every sector"),
    "log.verify_sample": ("Prüfung gestartet · Stichprobe", "Verification started · sample"),
    "log.verify_progress": ("{p} % geprüft", "{p} % verified"),
    "log.format": ("Formatieren gestartet", "Formatting started"),
    "log.done": ("Fertig", "Done"),
    "log.cancel_requested": ("Abbruch angefordert …", "Cancel requested …"),
    "log.stall": ("Kein Fortschritt seit {s} s", "No progress for {s} s"),
    "log.error": ("Fehler: {message}", "Error: {message}"),
    # Worker status messages (the worker speaks English; shown translated)
    "ws.processing": ("Datenträger {i}/{n}: {name}", "Disk {i}/{n}: {name}"),
    "ws.demo_create": ("Demo: virtuelle Datei wird angelegt", "Demo: creating the virtual disk file"),
    "ws.demo_size": ("Demo-Größe: {size}", "Demo size: {size}"),
    "ws.demo_wipe": ("Demo: Überschreiben mit {method}", "Demo: overwriting with {method}"),
    "ws.check": ("Platte erneut geprüft, {letters} wird ausgehängt",
                 "Disk checked again, unmounting {letters}"),
    "ws.open": ("{device} wird geöffnet", "Opening {device}"),
    "ws.size": ("Größe: {size}", "Size: {size}"),
    "ws.wipe": ("Überschreiben mit {method}", "Overwriting with {method}"),
    "ws.verify": ("Prüfung läuft", "Verifying"),
    "ws.reformat": ("Formatieren als {fs}", "Formatting as {fs}"),
    "ws.demo_reformat": ("Demo: formatiert als {fs}", "Demo: formatted as {fs}"),
    "ws.cert": ("Zertifikat wird erstellt und signiert", "Creating and signing the certificate"),
    "ws.done_ok": ("{name}: fertig", "{name}: done"),
    "ws.done_errors": ("{name}: Prüfung mit Fehlern", "{name}: verification found errors"),
    "ws.cancelled": ("Abgebrochen", "Cancelled"),
    "ws.error": ("Fehler bei {name}: {message}", "Error on {name}: {message}"),
    "ws.cancelled_user": ("Abgebrochen", "Cancelled"),
    # Result
    "result.eyebrow.ok": ("Ergebnis: Erfolgreich", "Result: Successful"),
    "result.eyebrow.limited": ("Ergebnis: Mit Einschränkungen", "Result: With limitations"),
    "result.eyebrow.failed": ("Ergebnis: Fehlgeschlagen", "Result: Failed"),
    "result.title.ok": ("{target} ist sicher gelöscht", "{target} is securely wiped"),
    "result.title.limited": ("{target} ist gelöscht — mit Einschränkungen",
                             "{target} is wiped — with limitations"),
    "result.title.failed": ("{target} wurde nicht sicher gelöscht", "{target} was not securely wiped"),
    "result.title.cancelled": ("Löschen von {target} abgebrochen", "Wiping {target} was cancelled"),
    "row.written": ("Überschrieben", "Overwritten"),
    "row.verified": ("Geprüft", "Verified"),
    "row.bad": ("Fehlerhafte Sektoren", "Bad sectors"),
    "row.formatted": ("Formatiert", "Formatted"),
    "row.duration": ("Dauer", "Duration"),
    "row.error": ("Fehler", "Error"),
    "val.pass_one": ("1 Durchgang", "1 pass"),
    "val.passes": ("{n} Durchgänge", "{n} passes"),
    "val.verified_full": ("jeder Sektor · {n} Abweichungen", "every sector · {n} mismatches"),
    "val.verified_sample": ("Stichprobe · {n} Abweichungen", "sample · {n} mismatches"),
    "val.verified_none": ("nicht geprüft", "not verified"),
    "val.bad_none": ("keine", "none"),
    "val.bad_some": ("{n} Bereiche", "{n} regions"),
    "val.formatted": ("{fs} · „{label}“", "{fs} · “{label}”"),
    "val.not_formatted": ("nein", "no"),
    "val.format_skipped": ("übersprungen", "skipped"),
    "result.open_cert": ("Zertifikat öffnen", "Open certificate"),
    "result.open_folder": ("Ordner öffnen", "Open folder"),
    "result.next": ("Nächsten Datenträger löschen", "Wipe the next disk"),
    "result.note_limited": (
        "Nicht beschreibbare Sektoren oder ein versteckter Bereich wurden nicht erreicht. "
        "Für vertrauliche Daten den Datenträger physisch vernichten.",
        "Unwritable sectors or a hidden area were not reached. "
        "For confidential data, destroy the disk physically.",
    ),
    "result.note_failed": (
        "Der Datenträger ist nicht sicher gelöscht. Einzelheiten stehen im Protokoll "
        "und im Audit-Log.",
        "The disk is not securely wiped. The details are in the log and the audit log.",
    ),
    "result.cert_eyebrow": ("Löschzertifikat · signiert", "Deletion certificate · signed"),
    "result.cert_eyebrow_none": ("Löschzertifikat", "Deletion certificate"),
    "result.cert_number": ("Nr. {n}", "No. {n}"),
    "result.cert_class": ("DIN 66399 Stufe {level} · NIST 800-88 {category}",
                          "DIN 66399 level {level} · NIST 800-88 {category}"),
    "result.operator": ("Bediener: {name}", "Operator: {name}"),
    "result.qr_text": (
        "Echtheit prüfen: Code scannen oder PDF in StickShredder öffnen.",
        "Check authenticity: scan the code or open the PDF in StickShredder.",
    ),
    "result.no_cert": ("Es wurde kein Zertifikat erstellt.", "No certificate was created."),
    "result.history": ("Im Verlauf gespeichert · Eintrag {n} · {chain}",
                       "Saved in the history · entry {n} · {chain}"),
    # Demo mode and messages
    "demo.title": ("Demo-Modus", "Demo mode"),
    "demo.text": (
        "Der Demo-Modus legt eine kleine virtuelle Datei (10 MB) an und führt damit den "
        "ganzen Ablauf aus: Überschreiben, Prüfen, Zertifikat. Echte Datenträger werden "
        "nicht berührt.",
        "Demo mode creates a small virtual disk file (10 MB) and runs the whole flow on "
        "it: overwrite, verify, certificate. No real disk is touched.",
    ),
    "demo.start": ("Demo starten", "Start demo"),
    "msg.refused_title": ("Löschen verweigert", "Wipe refused"),
    "msg.scan_failed_title": ("Datenträger nicht lesbar", "Cannot read disks"),
    "msg.scan_failed": (
        "Die Datenträger konnten nicht aufgelistet werden:\n{error}\n\n"
        "Läuft StickShredder als Administrator?",
        "The disks could not be listed:\n{error}\n\nIs StickShredder running as administrator?",
    ),
    "msg.busy_title": ("Löschen läuft", "Wipe running"),
    "msg.busy_text": (
        "Ein Löschvorgang läuft noch. Beim Beenden bleibt der Datenträger teilweise "
        "überschrieben. Trotzdem beenden?",
        "A wipe is still running. Quitting leaves the disk partly overwritten. Quit anyway?",
    ),
    "msg.open_failed": ("Konnte nicht geöffnet werden:\n{path}", "Could not be opened:\n{path}"),
    "about.text": (
        "Sicheres Löschen von Datenträgern mit signiertem Löschzertifikat "
        "(aufgebaut nach DIN 66399, ISO 21964 und NIST SP 800-88).\n\n"
        "Nicht von DEKRA, DIN oder einer anderen Stelle zertifiziert.\n\n"
        "MIT-Lizenz · © 2026 Robin Oertel",
        "Secure disk wiping with a signed deletion certificate "
        "(structured after DIN 66399, ISO 21964 and NIST SP 800-88).\n\n"
        "Not certified by DEKRA, DIN or any other body.\n\n"
        "MIT License · © 2026 Robin Oertel",
    ),
    "admin.title": ("Administratorrechte nötig", "Administrator rights required"),
    "admin.text": (
        "StickShredder läuft nicht als Administrator.\n\n"
        "Zum Löschen von Datenträgern sind Administratorrechte nötig. "
        "Bitte StickShredder als Administrator neu starten.",
        "StickShredder is not running as administrator.\n\n"
        "Wiping disks needs administrator rights. "
        "Please restart StickShredder as administrator.",
    ),
    # --- Wave 4: hardware erase ---
    "method.hardware": ("Hardware-Löschung + 1× Nullen", "Hardware erase + 1× zeros"),
    "note.beta": ("Beta", "Beta"),
    "note.hardware_recommended": ("Empfohlen · Beta", "Recommended · Beta"),
    "chip.hardware": ("Hardware-Löschung · Beta", "Hardware erase · Beta"),
    "type.demo_ssd": ("Demo-SSD", "Demo SSD"),
    "hw.card_title": ("Hardware-Löschung", "Hardware erase"),
    "hw.card_text": (
        "Die SSD löscht sich mit ihrem eingebauten Befehl selbst — auch Reserve-Bereiche, "
        "die Überschreiben nicht erreicht. Danach 1× Nullen und Prüfung.",
        "The SSD erases itself with its built-in command — including spare areas that "
        "overwriting cannot reach. Then 1× zeros and verification.",
    ),
    "hw.use": ("Verwenden …", "Use …"),
    "hw.use_overwrite": ("Nur überschreiben", "Overwrite only"),
    "hw.hint_sata_only": (
        "Hardware-Löschung (Beta) gibt es nur für SATA-SSDs, die direkt im PC "
        "angeschlossen sind — nicht im USB-Gehäuse und nicht bei NVMe.",
        "Hardware erase (Beta) is only available for SATA SSDs connected directly "
        "inside the PC — not in a USB enclosure and not for NVMe.",
    ),
    "hw.hint_refused": ("Hardware-Löschung hier nicht möglich: {reason}.",
                        "Hardware erase not possible here: {reason}."),
    "hw.short.no_path": ("der Anschluss reicht den Löschbefehl nicht weiter",
                         "the connection does not pass the erase command on"),
    "hw.short.unsupported": ("die SSD bietet sie nicht an", "the SSD does not offer it"),
    "hw.short.locked": ("die SSD ist mit einem Passwort gesperrt",
                        "the SSD is locked with a password"),
    "hw.overwrite_only_warning": (
        "Überschreiben allein erreicht die Reserve-Bereiche der SSD nicht — "
        "die Hardware-Löschung (Beta) schon.",
        "Overwriting alone cannot reach the SSD's spare areas — the hardware erase "
        "(Beta) can.",
    ),
    "select.estimate_hardware": (
        "ca. {time} plus Hardware-Löschung · du bestätigst danach noch einmal mit LÖSCHEN",
        "about {time} plus the hardware erase · you confirm once more by typing DELETE",
    ),
    "hw.method.ata-enhanced": ("Enhanced Secure Erase", "Enhanced Secure Erase"),
    "hw.method.ata-secure-erase": ("Secure Erase", "Secure Erase"),
    "hwdlg.window_title": ("Hardware-Löschung (Beta)", "Hardware erase (Beta)"),
    "hwdlg.title": ("Hardware-Löschung (Beta) verwenden?", "Use the hardware erase (Beta)?"),
    "hwdlg.text": (
        "Die SSD löscht sich mit ihrem eingebauten Befehl selbst (ATA Secure Erase). Das "
        "erreicht auch Reserve-Bereiche, die Überschreiben nicht erreicht. Danach "
        "überschreibt StickShredder jeden Sektor einmal mit Nullen und prüft das Ergebnis.",
        "The SSD erases itself with its built-in command (ATA Secure Erase). That also "
        "reaches spare areas overwriting cannot reach. Then StickShredder overwrites "
        "every sector once with zeros and verifies the result.",
    ),
    "hwdlg.beta": (
        "Beta: noch nicht auf echter Hardware getestet. Bei manchen SSDs und Anschlüssen "
        "kann sie fehlschlagen.",
        "Beta: not tested on real hardware yet. It can fail on some SSDs and connections.",
    ),
    "hwdlg.frozen": (
        "Viele PCs sperren SSDs beim Start („frozen“). Meldet die SSD das, bricht "
        "StickShredder vor dem Löschen ab: PC in den Standby schicken und wieder "
        "aufwecken oder die SSD abstecken und wieder anstecken, dann erneut versuchen.",
        "Many PCs lock SSDs at start-up (\"frozen\"). If the SSD reports that, StickShredder "
        "stops before erasing: put the PC to sleep and wake it, or unplug the SSD and "
        "plug it back in, then try again.",
    ),
    "hwdlg.awake": (
        "Während die SSD löscht: nicht abstecken und den PC nicht in den Standby "
        "schicken. StickShredder hält den PC so lange wach.",
        "While the SSD erases: do not unplug it and do not put the PC to sleep. "
        "StickShredder keeps the PC awake until then.",
    ),
    "hwdlg.password": (
        "Temporäres Passwort: {password} — bleibt die SSD nach einem Abbruch gesperrt, "
        "lässt sie sich damit entsperren.",
        "Temporary password: {password} — if the SSD stays locked after an interruption, "
        "it unlocks with this password.",
    ),
    "hwdlg.check": (
        "Ich verstehe, dass dieser Weg noch nicht auf echter Hardware getestet wurde.",
        "I understand that this path has not been tested on real hardware yet.",
    ),
    "hwdlg.confirm": ("Hardware-Löschung verwenden", "Use hardware erase"),
    "step.hardware": ("Löschbefehl", "Drive erase"),
    "tile.hw_method": ("Verfahren", "Method"),
    "tile.hw_estimate": ("SSD schätzt", "SSD estimate"),
    "tile.hw_password": ("Passwort", "Password"),
    "hw.method_short.ata-enhanced": ("Enhanced", "Enhanced"),
    "hw.method_short.ata-secure-erase": ("Normal", "Normal"),
    "hw.estimate_value": ("~ {n} Min.", "~ {n} min"),
    "hw.estimate_none": ("keine Angabe", "none given"),
    "hw.password_note": ("falls gesperrt", "if locked"),
    "running.hint_hw_check": (
        "Die SSD wird gefragt, ob sie sich selbst löschen kann.",
        "Asking the SSD whether it can erase itself.",
    ),
    "running.hint_hw_erase": (
        "Die SSD löscht sich jetzt selbst. Nicht abstecken, PC nicht in den Standby "
        "schicken — abbrechen geht in diesem Schritt nicht.",
        "The SSD is erasing itself. Do not unplug it or let the PC sleep — this step "
        "cannot be cancelled.",
    ),
    "log.hw_started": ("Hardware-Löschung gestartet · {method}", "Hardware erase started · {method}"),
    "log.hw_password": ("Temporäres Passwort: {password}", "Temporary password: {password}"),
    "log.hw_erased": ("SSD meldet: gelöscht ({time}) · jetzt 1× Nullen",
                      "SSD reports: erased ({time}) · now 1× zeros"),
    "log.hw_refused": ("Hardware-Löschung nicht möglich · nichts geschrieben",
                       "Hardware erase not possible · nothing written"),
    "log.hw_failed": ("Hardware-Löschung fehlgeschlagen", "Hardware erase failed"),
    "ws.hw_check": ("Hardware-Löschung wird geprüft", "Checking the hardware erase"),
    "hw.reason.frozen": (
        "Die SSD ist vom PC gesperrt („frozen“). PC in den Standby schicken und wieder "
        "aufwecken oder die SSD abstecken und wieder anstecken, dann erneut versuchen.",
        "The SSD is locked by the PC (\"frozen\"). Put the PC to sleep and wake it, or "
        "unplug the SSD and plug it back in, then try again.",
    ),
    "hw.reason.no_path": (
        "Der Anschluss reicht den Löschbefehl nicht weiter — das geht nur bei SATA direkt "
        "am Microsoft-Treiber.",
        "The connection does not pass the erase command on — that only works for SATA "
        "on Microsoft's driver.",
    ),
    "hw.reason.unsupported": ("Die SSD bietet keine Hardware-Löschung an.",
                              "The SSD does not offer a hardware erase."),
    "hw.reason.locked": ("Die SSD ist mit einem Passwort gesperrt.",
                         "The SSD is locked with a password."),
    "hw.reason.error": ("Die SSD konnte nicht abgefragt werden.",
                        "The SSD could not be asked."),
    "hw.reason.has_password": (
        "Die SSD hat schon ein Passwort, das nicht von dieser Löschung stammt. Ist es von "
        "einer abgebrochenen Hardware-Löschung übrig, kann StickShredder es entfernen.",
        "The SSD already has a password that is not from this erase. If it is left from "
        "an interrupted hardware erase, StickShredder can remove it.",
    ),
    "hw.short.has_password": ("die SSD hat schon ein Passwort", "the SSD already has a password"),
    "result.note_hw_has_password": (
        "Der Datenträger ist unverändert. Entferne zuerst das Passwort (falls es von "
        "StickShredder stammt) oder überschreibe ihn stattdessen.",
        "The disk is unchanged. Remove the password first (if it is StickShredder's) or "
        "overwrite it instead.",
    ),
    "msg.unlocking_title": ("SSD wird entsperrt", "Unlocking the SSD"),
    "msg.unlocking_text": (
        "Die SSD wird gerade entsperrt. Das dauert nur Sekunden — bitte warten, dann "
        "kannst du StickShredder beenden.",
        "The SSD is being unlocked. That takes only seconds — please wait, then you can "
        "quit StickShredder.",
    ),
    "result.title.hw_refused": ("Hardware-Löschung nicht möglich — {target} ist unverändert",
                                "Hardware erase not possible — {target} is unchanged"),
    "row.hardware": ("Hardware-Löschung", "Hardware erase"),
    "row.reason": ("Grund", "Reason"),
    "val.hw_erased": ("die SSD hat sich selbst gelöscht · {method} · {time}",
                      "the SSD erased itself · {method} · {time}"),
    "val.hw_refused": ("nicht gestartet — nichts wurde geschrieben",
                       "not started — nothing was written"),
    "val.hw_failed": ("abgebrochen: {detail}", "stopped: {detail}"),
    "result.note_hw_refused": (
        "Der Datenträger ist unverändert. Du kannst ihn stattdessen überschreiben "
        "(1× Nullen mit Prüfung).",
        "The disk is unchanged. You can overwrite it instead (1× zeros with verification).",
    ),
    "result.note_hw_failed": (
        "Der Datenträger ist nicht sicher gelöscht. Die SSD hat eventuell noch das "
        "temporäre Passwort „{password}“ — ist sie nach dem Abstecken gesperrt, damit "
        "entsperren. Danach kannst du sie überschreiben.",
        "The disk is not securely wiped. The SSD may still have the temporary password "
        "\"{password}\" — if it is locked after replugging, unlock it with that password. "
        "Then you can overwrite it.",
    ),
    "result.overwrite_instead": ("Stattdessen überschreiben", "Overwrite instead"),
    "hw.hint_beta_off": (
        "Hardware-Löschung (Beta) ist für diese SSD möglich — einschalten unter "
        "Optionen → Beta-Funktionen.",
        "Hardware erase (Beta) is possible for this SSD — switch it on under "
        "Options → Beta features.",
    ),
    "settings.section_beta": ("Beta-Funktionen", "Beta features"),
    "settings.beta_hint": (
        "Schaltet die Hardware-Löschung (Beta) für SATA-SSDs frei, die direkt im PC "
        "angeschlossen sind: Die SSD löscht sich selbst, danach 1× Nullen mit Prüfung. "
        "Noch nicht auf echter Hardware getestet.",
        "Unlocks the hardware erase (Beta) for SATA SSDs connected directly inside the "
        "PC: the SSD erases itself, then 1× zeros with verification. Not tested on real "
        "hardware yet.",
    ),
    "settings.beta_check": ("Beta-Funktionen aktivieren", "Enable Beta features"),
    "msg.erasing_title": ("Die SSD löscht sich gerade", "The SSD is erasing itself"),
    "msg.erasing_text": (
        "StickShredder kann jetzt nicht beendet werden: Die SSD löscht sich mit ihrem "
        "eigenen Befehl, und der lässt sich nicht abbrechen. Bitte warten, bis sie fertig "
        "ist — nicht abstecken und den PC nicht ausschalten.\n\n"
        "Falls die SSD danach gesperrt ist: temporäres Passwort „{password}“.",
        "StickShredder cannot quit now: the SSD is erasing itself with its own command, "
        "which cannot be cancelled. Please wait until it is done — do not unplug it and "
        "do not switch the PC off.\n\n"
        "If the SSD is locked afterwards: temporary password \"{password}\".",
    ),
    "result.note_hw_locked": (
        "Die SSD ist mit einem Passwort gesperrt und lässt sich so weder löschen noch "
        "beschreiben. Stammt die Sperre von einer abgebrochenen Hardware-Löschung, kann "
        "StickShredder sie entsperren.",
        "The SSD is locked with a password, so it can be neither erased nor written. "
        "If the lock comes from an interrupted hardware erase, StickShredder can unlock it.",
    ),
    "unlock.button": ("SSD entsperren …", "Unlock the SSD …"),
    "unlock.link": ("Entsperren …", "Unlock …"),
    "unlock.title": ("SSD entsperren?", "Unlock the SSD?"),
    "unlock.text": (
        "StickShredder versucht einmal, {target} mit dem temporären Passwort „{password}“ "
        "zu entsperren und das Passwort zu entfernen. Stammt die Sperre nicht von "
        "StickShredder, zählt das als Fehlversuch — nach etwa fünf nimmt die SSD erst "
        "nach dem Abstecken wieder ein Passwort an.",
        "StickShredder tries once to unlock {target} with the temporary password "
        "\"{password}\" and to remove it. If the lock is not StickShredder's, this counts "
        "as a failed attempt — after about five the SSD only takes a password again once "
        "it was unplugged.",
    ),
    "unlock.done_title": ("SSD entsperren", "Unlock the SSD"),
    "unlock.state.unlocked": (
        "Entsperrt: Das temporäre Passwort ist entfernt. Du kannst die SSD jetzt löschen.",
        "Unlocked: the temporary password is removed. You can wipe the SSD now.",
    ),
    "unlock.state.no_password": ("Die SSD hat kein Passwort — sie ist nicht gesperrt.",
                                 "The SSD has no password — it is not locked."),
    "unlock.state.frozen": (
        "Die SSD ist vom PC gesperrt („frozen“). PC in den Standby schicken und wieder "
        "aufwecken oder die SSD abstecken und wieder anstecken, dann erneut versuchen.",
        "The SSD is locked by the PC (\"frozen\"). Put the PC to sleep and wake it, or "
        "unplug the SSD and plug it back in, then try again.",
    ),
    "unlock.state.expired": (
        "Zu viele Fehlversuche: Die SSD nimmt erst wieder ein Passwort an, wenn sie "
        "abgesteckt und wieder angesteckt wurde.",
        "Too many failed attempts: the SSD only takes a password again after it was "
        "unplugged and plugged back in.",
    ),
    "unlock.state.wrong_password": (
        "Das Passwort passt nicht: Die Sperre stammt nicht von StickShredder. Es gibt "
        "keinen zweiten Versuch.",
        "The password does not fit: the lock is not StickShredder's. There is no "
        "second try.",
    ),
    "unlock.state.unsupported": (
        "Der Anschluss reicht den Befehl nicht weiter — das geht nur bei SATA direkt am "
        "Microsoft-Treiber.",
        "The connection does not pass the command on — that only works for SATA on "
        "Microsoft's driver.",
    ),
    "unlock.state.error": ("Entsperren fehlgeschlagen: {error}", "Unlock failed: {error}"),
    # --- Wave 4: pages ---
    # Zertifikate
    "certs.title": ("Zertifikate", "Certificates"),
    "certs.subtitle": (
        "Jede Löschung dieses PCs mit ihrem Löschzertifikat.",
        "Every wipe on this PC with its deletion certificate.",
    ),
    "certs.verify_button": ("Zertifikat prüfen", "Check a certificate"),
    "certs.search": ("Suchen: Gerät, Seriennummer, Zertifikat …",
                     "Search: device, serial number, certificate …"),
    "certs.open_pdf": ("PDF öffnen", "Open PDF"),
    "certs.open_folder": ("Ordner öffnen", "Open folder"),
    "certs.export": ("CSV exportieren", "Export CSV"),
    "certs.col.date": ("Datum", "Date"),
    "certs.col.device": ("Gerät", "Device"),
    "certs.col.serial": ("Seriennummer", "Serial number"),
    "certs.col.method": ("Methode", "Method"),
    "certs.col.result": ("Ergebnis", "Result"),
    "certs.col.cert": ("Zertifikat", "Certificate"),
    "certs.result.ok": ("OK", "OK"),
    "certs.result.limited": ("EINGESCHRÄNKT", "LIMITED"),
    "certs.result.failed": ("FEHLGESCHLAGEN", "FAILED"),
    "certs.empty_text": (
        "Nach der ersten Löschung steht sie hier mit ihrem Zertifikat. Ein Zertifikat "
        "von einem anderen PC kannst du schon jetzt prüfen.",
        "After the first wipe it is listed here with its certificate. You can already "
        "check a certificate from another PC.",
    ),
    "certs.no_match": ("Kein Eintrag passt zur Suche.", "No entry matches the search."),
    "certs.chain_ok": ("Verlauf unverändert · {n} Einträge geprüft",
                       "History unchanged · {n} entries checked"),
    "certs.chain_ok_one": ("Verlauf unverändert · 1 Eintrag geprüft",
                           "History unchanged · 1 entry checked"),
    "certs.chain_legacy": ("{n} ältere ohne Prüfsumme (vor 2.0)",
                           "{n} older ones without checksum (before 2.0)"),
    "certs.chain_broken": ("Verlauf verändert ab Eintrag {n}: {reason}",
                           "History altered from entry {n}: {reason}"),
    "certs.no_cert": ("Für diese Löschung gibt es kein Zertifikat.",
                      "This wipe has no certificate."),
    "certs.pdf_missing": ("Kein PDF für {cert} im Zertifikatsordner gefunden.",
                          "No PDF for {cert} found in the certificate folder."),
    "certs.open_failed": ("Konnte nicht geöffnet werden: {path}", "Could not be opened: {path}"),
    "certs.exported_one": ("1 Eintrag exportiert: {path}", "1 entry exported: {path}"),
    "certs.exported": ("{n} Einträge exportiert: {path}", "{n} entries exported: {path}"),
    "certs.export_failed": ("Export fehlgeschlagen: {error}", "Export failed: {error}"),
    "certs.export_title": ("Verlauf exportieren", "Export history"),
    "certs.csv_filter": ("CSV-Dateien (*.csv)", "CSV files (*.csv)"),
    "certs.pick_title": ("Zertifikat zum Prüfen wählen", "Choose a certificate to check"),
    "certs.pdf_filter": ("PDF-Dateien (*.pdf)", "PDF files (*.pdf)"),
    "certs.verify.eyebrow": ("Prüfergebnis", "Check result"),
    "certs.verify.trusted": ("Echt, von einem vertrauten Schlüssel",
                             "Genuine, from a trusted key"),
    "certs.verify.unknown_key": ("Signatur gültig, Schlüssel unbekannt",
                                 "Signature valid, key unknown"),
    "certs.verify.invalid": ("Ungültig oder verändert", "Invalid or altered"),
    "certs.verify.trusted_text": (
        "Das Zertifikat ist seit der Erstellung unverändert und mit dem vertrauten "
        "Schlüssel „{name}“ signiert.",
        "The certificate is unchanged since it was created and signed with the trusted "
        "key “{name}”.",
    ),
    "certs.verify.unknown_text": (
        "Das Zertifikat ist seit der Erstellung unverändert, aber sein Schlüssel ist hier "
        "nicht als vertraut hinterlegt. Vergleiche den Fingerabdruck mit der Schlüsselkarte "
        "der Firma, die es ausgestellt hat.",
        "The certificate is unchanged since it was created, but its key is not trusted "
        "here. Compare the fingerprint with the key card of the company that issued it.",
    ),
    "certs.verify.invalid_text": ("Diesem Zertifikat nicht vertrauen. Grund: {reason}",
                                  "Do not trust this certificate. Reason: {reason}"),
    "certs.verify.note": (
        "Die Signatur zeigt, dass das Zertifikat unverändert ist — nicht, dass physisch "
        "gelöscht wurde.",
        "The signature shows that the certificate is unchanged — not that data was "
        "physically erased.",
    ),
    "certs.verify.close": ("Schließen", "Close"),
    # Schlüssel
    "keys.title": ("Schlüssel", "Keys"),
    "keys.subtitle": (
        "Mit diesem Schlüssel signiert StickShredder jedes Löschzertifikat dieses PCs.",
        "StickShredder signs every deletion certificate from this PC with this key.",
    ),
    "keys.own_eyebrow": ("Schlüssel dieses PCs · Fingerabdruck", "This PC's key · fingerprint"),
    "keys.copy": ("Kopieren", "Copy"),
    "keys.copied": ("Fingerabdruck kopiert", "Fingerprint copied"),
    "keys.created": ("Auf diesem PC seit {date}", "On this PC since {date}"),
    "keys.unreadable": ("Schlüssel nicht lesbar: {error}", "Key not readable: {error}"),
    "keys.private_note": (
        "Der private Schlüssel verlässt diesen PC nie und wird nirgends angezeigt.",
        "The private key never leaves this PC and is never shown.",
    ),
    "keys.keycard_button": ("Schlüsselkarte erstellen", "Create key card"),
    "keys.keycard_title": ("Wofür ist die Schlüsselkarte?", "What is the key card for?"),
    "keys.keycard_text": (
        "Ein Fingerabdruck auf dem Zertifikat beweist allein nichts — ein Fälscher kann "
        "seinen eigenen aufdrucken. Die Schlüsselkarte ist eine PDF-Seite mit diesem "
        "Fingerabdruck. Lege sie in der IT-Dokumentation ab oder gib sie Kunden und "
        "Prüfern: Damit sehen sie, dass ein Zertifikat wirklich von diesem PC stammt.",
        "A fingerprint on a certificate proves nothing on its own — a forger can print "
        "their own. The key card is a one-page PDF with this fingerprint. File it with "
        "the IT documentation or give it to customers and auditors: it lets them see that "
        "a certificate really comes from this PC.",
    ),
    "keys.company_missing": (
        "Kein Firmenname eingestellt — er fehlt dann auf der Karte. Unter Optionen eintragen.",
        "No company name set — it will be missing on the card. Enter it under Options.",
    ),
    "keys.keycard_saved": ("Schlüsselkarte gespeichert: {path}", "Key card saved: {path}"),
    "keys.keycard_failed": ("Schlüsselkarte konnte nicht erstellt werden: {error}",
                            "Key card could not be created: {error}"),
    "keys.trusted_title": ("Vertraute Schlüssel", "Trusted keys"),
    "keys.trusted_text": (
        "Zertifikate mit einem dieser Schlüssel gelten beim Prüfen als echt.",
        "Certificates signed with one of these keys count as genuine when checked.",
    ),
    "keys.add": ("Aus Schlüsselkarte hinzufügen …", "Add from key card …"),
    "keys.rename": ("Umbenennen", "Rename"),
    "keys.remove": ("Entfernen", "Remove"),
    "keys.own_chip": ("Dieser PC", "This PC"),
    "keys.added_on": ("vertraut seit {date}", "trusted since {date}"),
    "keys.none": ("Noch keine vertrauten Schlüssel.", "No trusted keys yet."),
    "keys.pick_title": ("Schlüsselkarte wählen", "Choose a key card"),
    "keys.name_title": ("Name für den Schlüssel", "Name for the key"),
    "keys.name_label": ("Wem gehört dieser Schlüssel? (z. B. Firma oder PC)",
                        "Whose key is this? (e.g. company or PC)"),
    "keys.name_empty": ("Der Name darf nicht leer sein.", "The name must not be empty."),
    "keys.not_a_keycard": ("Das ist keine Schlüsselkarte von StickShredder.",
                           "This is not a StickShredder key card."),
    "keys.file_unreadable": ("Datei nicht lesbar: {error}", "File not readable: {error}"),
    "keys.added": (
        "„{name}“ ist jetzt vertraut. Vergleiche den Fingerabdruck mit der gedruckten "
        "Karte: {fingerprint}",
        "“{name}” is now trusted. Compare the fingerprint with the printed card: "
        "{fingerprint}",
    ),
    "keys.already": ("Dieser Schlüssel ist schon vertraut: „{name}“.",
                     "This key is already trusted: “{name}”."),
    "keys.renamed": ("Umbenannt in „{name}“.", "Renamed to “{name}”."),
    "keys.remove_title": ("Schlüssel entfernen?", "Remove key?"),
    "keys.remove_text": (
        "„{name}“ nicht mehr vertrauen? Zertifikate mit diesem Schlüssel zeigen beim "
        "Prüfen dann „Schlüssel unbekannt“.",
        "Stop trusting “{name}”? Certificates signed with this key then show "
        "“key unknown” when checked.",
    ),
    "keys.remove_own": ("Das ist der Schlüssel dieses PCs.", "This is this PC's own key."),
    "keys.removed": ("„{name}“ entfernt.", "“{name}” removed."),
    "keys.trust_damaged": (
        "Die Liste vertrauter Schlüssel ist beschädigt und bleibt unverändert: {path}",
        "The trusted key list is damaged and is left unchanged: {path}",
    ),
    # Optionen
    "settings.title": ("Optionen", "Options"),
    "settings.subtitle": (
        "Firmendaten fürs Zertifikat, Voreinstellungen zum Löschen und Darstellung.",
        "Company details for the certificate, wipe defaults and appearance.",
    ),
    "settings.save": ("Speichern", "Save"),
    "settings.reset": ("Zurücksetzen", "Reset"),
    "settings.saved": ("Gespeichert", "Saved"),
    "settings.save_failed": ("Konnte nicht gespeichert werden: {error}",
                             "Could not be saved: {error}"),
    "settings.section_cert": ("Zertifikat", "Certificate"),
    "settings.section_cert_hint": (
        "Steht auf jedem Löschzertifikat und auf der Schlüsselkarte.",
        "Shown on every deletion certificate and on the key card.",
    ),
    "settings.company": ("Firmenname", "Company name"),
    "settings.company_placeholder": ("z. B. ACME IT GmbH", "e.g. ACME IT Ltd"),
    "settings.address": ("Adresse", "Address"),
    "settings.logo": ("Logo", "Logo"),
    "settings.logo_placeholder": ("Kein Logo (PNG oder JPG)", "No logo (PNG or JPG)"),
    "settings.browse": ("Durchsuchen …", "Browse …"),
    "settings.clear": ("Entfernen", "Remove"),
    "settings.cert_dir": ("Zertifikatsordner", "Certificate folder"),
    "settings.cert_language": ("Sprache der Zertifikate", "Certificate language"),
    "settings.lang_de": ("Deutsch", "German"),
    "settings.lang_en": ("Englisch", "English"),
    "settings.lang_both": ("Zweisprachig (Deutsch und Englisch)",
                           "Bilingual (German and English)"),
    "settings.section_wipe": ("Voreinstellungen zum Löschen", "Wipe defaults"),
    "settings.section_wipe_hint": (
        "Gelten für jeden neuen Löschvorgang. Einzeln änderbar über „Andere Methode …“.",
        "Apply to every new wipe. Change them per wipe with “Other method …”.",
    ),
    "settings.section_look": ("Darstellung", "Appearance"),
    "settings.section_look_hint": ("Wirkt sofort.", "Takes effect at once."),
    "settings.theme": ("Design", "Theme"),
    "settings.theme_light": ("Hell", "Light"),
    "settings.theme_dark": ("Dunkel", "Dark"),
    "settings.theme_system": ("Wie Windows", "Like Windows"),
    "settings.language": ("Sprache", "Language"),
    "settings.ui_de": ("Deutsch", "Deutsch"),
    "settings.ui_en": ("English", "English"),
    "settings.demo_text": (
        "Probiere den ganzen Ablauf an einer kleinen virtuellen Datei aus. Echte "
        "Datenträger werden nicht berührt.",
        "Try the whole flow on a small virtual file. No real disk is touched.",
    ),
    "settings.section_about": ("Über StickShredder", "About StickShredder"),
    "settings.version": ("Version {version}", "Version {version}"),
    "settings.github": ("Auf GitHub ansehen", "View on GitHub"),
    "settings.err_company": (
        "Firmenname fehlt — er ist nötig, wenn eine Adresse eingetragen ist.",
        "Company name missing — it is required when an address is entered.",
    ),
    "settings.err_operator": ("Der Bedienername braucht mindestens 2 Zeichen.",
                              "The operator name needs at least 2 characters."),
    "settings.pick_logo": ("Firmenlogo wählen", "Choose company logo"),
    "settings.image_filter": ("Bilder (*.png *.jpg *.jpeg *.bmp)",
                              "Images (*.png *.jpg *.jpeg *.bmp)"),
    "settings.pick_folder": ("Zertifikatsordner wählen", "Choose certificate folder"),
    # --- end Wave 4: pages ---
}


def set_language(lang: str) -> None:
    """Switch the UI language; anything unknown falls back to German."""
    global _language
    _language = lang if lang in LANGUAGES else "de"


def language() -> str:
    return _language


def tr(key: str, **values) -> str:
    """Text for ``key`` in the active language, placeholders filled."""
    pair = STRINGS.get(key)
    if pair is None:
        return key
    text = pair[1] if _language == "en" else pair[0]
    return text.format(**values) if values else text


def serial_label(serial) -> str:
    """"SN <serial>", or "SN not reported by the device" (empty / all zeros)."""
    return tr("serial", serial=serial_text(serial, _language))


def pick(de: str, en: str) -> str:
    """The German or English variant of a text that lives elsewhere."""
    return en if _language == "en" else de


# -- Formatting ------------------------------------------------------------------


def fmt_number(value: float, decimals: int = 0) -> str:
    text = f"{value:,.{decimals}f}"
    if _language == "de":
        text = text.replace(",", " ").replace(".", ",").replace(" ", ".")
    return text


def fmt_size(size_bytes: int) -> str:
    """Disk size the way Windows shows it (binary units, labelled GB/MB/TB)."""
    size = max(0, int(size_bytes or 0))
    gib = size / 1024 ** 3
    if gib >= 1024:
        return f"{fmt_number(gib / 1024, 1)} TB"
    if gib >= 1:
        return f"{fmt_number(gib, 1)} GB"
    return f"{fmt_number(size / 1024 ** 2)} MB"


def fmt_duration(seconds: float) -> str:
    """Elapsed time: ``42 Sek.``, ``17 Min. 42 Sek.``, ``2 Std. 5 Min.``"""
    total = max(0, int(round(seconds or 0)))
    if total < 60:
        return tr("dur.seconds", s=total)
    if total < 3600:
        return tr("dur.minutes", m=total // 60, s=total % 60)
    return tr("dur.hours", h=total // 3600, m=(total % 3600) // 60)


def fmt_estimate(seconds: float) -> str:
    """Rough duration for planning: ``unter 1 Minute``, ``18 Minuten`` …"""
    total = max(0, int(seconds or 0))
    if total < 60:
        return tr("time.under_minute")
    if total < 3600:
        minutes = round(total / 60)
        return tr("time.one_minute") if minutes == 1 else tr("time.minutes", n=minutes)
    return tr("time.hours", h=total // 3600, m=(total % 3600) // 60)


def fmt_speed(mb_per_s: float) -> str:
    if mb_per_s >= 1024:
        return f"{fmt_number(mb_per_s / 1024, 1)} GB/s"
    if mb_per_s >= 10:
        return f"{fmt_number(mb_per_s)} MB/s"
    if mb_per_s >= 1:
        return f"{fmt_number(mb_per_s, 1)} MB/s"
    if mb_per_s > 0:
        return f"{fmt_number(mb_per_s * 1024)} KB/s"
    return "– MB/s"


def fmt_eta(seconds: float) -> str:
    total = max(0, int(seconds))
    if total < 60:
        text = tr("dur.seconds", s=total)
    elif total < 3600:
        text = pick(f"{round(total / 60)} Min.", f"{round(total / 60)} min")
    else:
        text = tr("dur.hours", h=total // 3600, m=(total % 3600) // 60)
    return tr("eta.value", time=text)


def language_half(text: str) -> str:
    """Pick one side of a legacy ``"Deutsch / English"`` message."""
    text = str(text or "")
    if " / " not in text:
        return text
    german, english = text.split(" / ", 1)
    return english.strip() if _language == "en" else german.strip()
