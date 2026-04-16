# DIN 66399 / ISO 21964 -- Informationen zur Datenträgervernichtung

**(Information on Data Carrier Destruction)**

Dieses Dokument erklärt die DIN 66399 / ISO 21964 Norm für IT-Administratoren, die keine Spezialisten für Datenschutznormen sind. Englische Übersetzungen sind in Klammern angegeben.

This document explains the DIN 66399 / ISO 21964 standard for IT administrators who are not specialists in data protection standards. English translations are provided in parentheses.

---

## Inhaltsverzeichnis (Table of Contents)

- [Was ist DIN 66399?](#was-ist-din-66399)
- [Schutzklassen (Protection Classes)](#schutzklassen-protection-classes)
- [Sicherheitsstufen (Security Levels)](#sicherheitsstufen-security-levels)
- [Zuordnung: Schutzklasse zu Sicherheitsstufe](#zuordnung-schutzklasse-zu-sicherheitsstufe)
- [Wie StickShredder diese Stufen abbildet](#wie-stickshredder-diese-stufen-abbildet)
- [Logische vs. physische Vernichtung](#logische-vs-physische-vernichtung)
- [Wann ist physische Vernichtung erforderlich?](#wann-ist-physische-vernichtung-erforderlich)
- [Referenzen](#referenzen)

---

## Was ist DIN 66399?

**(What is DIN 66399?)**

Die **DIN 66399** ist eine deutsche Norm, die 2012 vom Deutschen Institut für Normung (DIN) veröffentlicht wurde. Sie wurde 2018 als **ISO 21964** auch international übernommen. Die Norm legt Anforderungen an die Vernichtung von Datenträgern fest und ersetzt die frühere DIN 32757, die nur Papiervernichtung abdeckte.

(DIN 66399 is a German standard published in 2012 by the German Institute for Standardization. It was adopted internationally as ISO 21964 in 2018. The standard defines requirements for the destruction of data carriers and replaces the earlier DIN 32757, which covered only paper destruction.)

Die Norm besteht aus drei Teilen:

| Teil (Part) | Inhalt (Content) |
|---|---|
| **DIN 66399-1** | Grundlagen und Begriffe (Principles and definitions) |
| **DIN 66399-2** | Anforderungen an Maschinen zur Vernichtung (Requirements for destruction machines) |
| **DIN 66399-3** | Prozess der Datenträgervernichtung (Process of data carrier destruction) |

Die Norm definiert **Schutzklassen** (Protection Classes), **Sicherheitsstufen** (Security Levels) und **Datenträgertypen** (Media Types), um eine einheitliche Klassifizierung und Dokumentation der Datenvernichtung zu ermöglichen.

(The standard defines Protection Classes, Security Levels, and Media Types to enable a uniform classification and documentation of data destruction.)

---

## Schutzklassen (Protection Classes)

Die DIN 66399 unterscheidet drei Schutzklassen, die sich nach dem Schutzbedarf der Daten richten:

(DIN 66399 distinguishes three protection classes based on the protection needs of the data.)

### Schutzklasse 1 -- Normaler Schutzbedarf (Normal Protection)

**Betrifft:** Interne Daten, deren Offenlegung negative Auswirkungen auf das Unternehmen haben könnte, die aber nicht personenbezogen oder vertraulich im engeren Sinne sind.

(Concerns: Internal data whose disclosure could have negative effects on the company but which is not personal or confidential in the strict sense.)

**Beispiele (Examples):**
- Allgemeine Geschäftskorrespondenz (General business correspondence)
- Produktkataloge und Preislisten (Product catalogs and price lists)
- Allgemeine interne Mitteilungen (General internal communications)
- Nicht-sensible Marketingmaterialien (Non-sensitive marketing materials)
- Veraltete Entwürfe und Notizen (Outdated drafts and notes)

### Schutzklasse 2 -- Hoher Schutzbedarf (High Protection)

**Betrifft:** Vertrauliche Daten, deren Offenlegung erhebliche negative Auswirkungen auf das Unternehmen haben könnte, einschließlich Verletzung von Verträgen oder Gesetzen.

(Concerns: Confidential data whose disclosure could have significant negative effects on the company, including violations of contracts or laws.)

**Beispiele (Examples):**
- Personenbezogene Daten nach DSGVO (Personal data under GDPR)
- Finanzbuchhaltung und Bilanzdaten (Financial accounting and balance sheet data)
- Personalakten (Personnel files)
- Verträge und Angebote (Contracts and offers)
- Steuerdaten und Wirtschaftsprüfungsunterlagen (Tax data and audit documents)
- Kundendaten und Geschäftsgeheimnisse (Customer data and trade secrets)

### Schutzklasse 3 -- Sehr hoher Schutzbedarf (Very High Protection)

**Betrifft:** Streng geheime Daten, deren Offenlegung existenzbedrohende Auswirkungen auf das Unternehmen oder Leib und Leben von Personen haben könnte.

(Concerns: Top-secret data whose disclosure could have existence-threatening effects on the company or endanger the life and limb of individuals.)

**Beispiele (Examples):**
- Geheimdienstliche und militärische Daten (Intelligence and military data)
- Zeugenschutzprogramm-Daten (Witness protection program data)
- Forschungs- und Entwicklungsgeheimnisse von strategischer Bedeutung (R&D secrets of strategic importance)
- Geheime Regierungsdokumente (Classified government documents)
- Daten, deren Offenlegung strafrechtliche Konsequenzen nach sich ziehen kann (Data whose disclosure may result in criminal consequences)

---

## Sicherheitsstufen (Security Levels)

Für den Datenträgertyp **H** (Festplatten und digitale Speichermedien / Hard drives and digital storage media) definiert DIN 66399 sieben Sicherheitsstufen:

(For media type H -- hard drives and digital storage media -- DIN 66399 defines seven security levels.)

### H-1: Allgemeine Daten (General Data)

**Vernichtungsanforderung (Destruction requirement):** Datenträger funktionsuntüchtig machen (Render data carrier non-functional).

Daten sind mit einfachem Aufwand nicht reproduzierbar. Die Löschung erfordert kein spezielles Verfahren.

(Data cannot be reproduced with simple effort. Deletion requires no special method.)

### H-2: Interne Daten (Internal Data)

**Vernichtungsanforderung:** Datenträger beschädigen oder überschreiben (Damage or overwrite data carrier).

Daten sind mit besonderem Aufwand nicht reproduzierbar. Einfaches Überschreiben ist ausreichend.

(Data cannot be reproduced with special effort. Simple overwriting is sufficient.)

### H-3: Sensible Daten (Sensitive Data)

**Vernichtungsanforderung:** Datenträger verformen, mehrfach überschreiben (Deform data carrier, overwrite multiple times).

Daten sind mit erheblichem Aufwand nicht reproduzierbar. Mehrfaches Überschreiben mit verschiedenen Mustern ist erforderlich.

(Data cannot be reproduced with considerable effort. Multiple overwriting with different patterns is required.)

### H-4: Besonders sensible Daten (Particularly Sensitive Data)

**Vernichtungsanforderung:** Datenträger mehrfach verformen oder überschreiben nach anerkanntem Standard (Deform data carrier multiple times or overwrite according to recognized standard).

Daten sind mit außergewöhnlichem Aufwand nicht reproduzierbar. Überschreibung nach einem anerkannten Standard (z.B. BSI VSITR) ist erforderlich.

(Data cannot be reproduced with extraordinary effort. Overwriting according to a recognized standard -- e.g., BSI VSITR -- is required.)

### H-5: Geheim zu haltende Daten (Data to be Kept Secret)

**Vernichtungsanforderung:** Datenträger zerkleinern oder zerstören (Shred or destroy data carrier).

Daten sind nach dem Stand der Technik nicht reproduzierbar. Physische Vernichtung oder ein vergleichbares Verfahren ist erforderlich.

(Data cannot be reproduced according to the state of the art. Physical destruction or an equivalent method is required.)

### H-6: Geheime Hochsicherheitsdaten (Secret High-Security Data)

**Vernichtungsanforderung:** Datenträger in kleine Partikel zerkleinern (Shred data carrier into small particles).

Daten sind auch mit modernsten Labormethoden nicht reproduzierbar. Feinzerlegung ist erforderlich.

(Data cannot be reproduced even with the most advanced laboratory methods. Fine disassembly is required.)

### H-7: Hochgeheime Daten (Top-Secret Data)

**Vernichtungsanforderung:** Datenträger in kleinste Partikel zerkleinern (Shred data carrier into smallest particles).

Dies ist die höchste Sicherheitsstufe. Militärische/geheimdienstliche Anforderungen. Spezialmaschinen erforderlich.

(This is the highest security level. Military/intelligence requirements. Special machines required.)

---

## Zuordnung: Schutzklasse zu Sicherheitsstufe

**(Mapping: Protection Class to Security Level)**

Die folgende Tabelle zeigt, welche Sicherheitsstufen für welche Schutzklassen zulässig bzw. empfohlen sind:

(The following table shows which security levels are permitted or recommended for which protection classes.)

| Schutzklasse (Protection Class) | Mindest-Sicherheitsstufe (Minimum Security Level) | Empfohlener Bereich (Recommended Range) |
|---|---|---|
| **Schutzklasse 1** -- Normaler Schutzbedarf | H-1 | H-1 bis H-3 |
| **Schutzklasse 2** -- Hoher Schutzbedarf | H-3 | H-3 bis H-5 |
| **Schutzklasse 3** -- Sehr hoher Schutzbedarf | H-5 | H-5 bis H-7 |

> **Wichtig (Important):** Die Schutzklasse legt den *Mindeststandard* fest. Eine höhere Sicherheitsstufe ist immer zulässig, aber niemals eine niedrigere. Die konkrete Sicherheitsstufe sollte in Abstimmung mit dem Datenschutzbeauftragten und/oder der IT-Sicherheitsbeauftragten festgelegt werden.
>
> (The protection class sets the *minimum standard*. A higher security level is always permitted, but never a lower one. The specific security level should be determined in consultation with the data protection officer and/or the IT security officer.)

---

## Wie StickShredder diese Stufen abbildet

**(How StickShredder Maps to These Levels)**

StickShredder ist ein softwarebasiertes Überschreibtool. Es kann logische Löschung (Überschreiben) durchführen, aber keine physische Vernichtung. Daher deckt es die Sicherheitsstufen **H-1 bis H-4** ab:

(StickShredder is a software-based overwriting tool. It can perform logical deletion -- overwriting -- but not physical destruction. Therefore, it covers security levels H-1 through H-4.)

| StickShredder-Methode | DIN 66399 Stufe | Geeignet für Schutzklasse |
|---|---|---|
| Zero-Fill (1 Durchlauf) | H-1 / H-2 | Schutzklasse 1 |
| 3-Pass Random (3 Durchläufe) | H-3 | Schutzklasse 1, bedingt Schutzklasse 2 |
| BSI VSITR (7 Durchläufe) | H-4 | Schutzklasse 1-2 |
| Benutzerdefiniert | Variiert | Abhängig von Konfiguration |

> **Einschränkung bei SSDs/Flash (Limitation for SSDs/Flash):** Die oben genannten Zuordnungen gelten primär für herkömmliche Festplatten (HDDs). Bei SSDs und Flash-basierten USB-Datenträgern kann aufgrund von Wear Leveling und Over-Provisioning nicht garantiert werden, dass alle physischen Speicherzellen überschrieben werden. Für SSDs mit Daten der Schutzklasse 2 oder höher wird physische Vernichtung oder ATA Secure Erase empfohlen.
>
> (The mappings above apply primarily to traditional hard drives. For SSDs and flash-based USB drives, wear leveling and over-provisioning mean that overwriting all physical memory cells cannot be guaranteed. For SSDs with data of protection class 2 or higher, physical destruction or ATA Secure Erase is recommended.)

---

## Logische vs. physische Vernichtung

**(Logical vs. Physical Destruction)**

Es gibt zwei grundsätzlich verschiedene Ansätze zur Datenvernichtung:

(There are two fundamentally different approaches to data destruction.)

### Logische Vernichtung (Logical Destruction)

- **Methode:** Überschreiben der Daten auf dem Datenträger mit neuen Daten (Nullen, Zufallsdaten, Muster)
- **Werkzeuge:** Software wie StickShredder, nwipe, DBAN, Eraser
- **Vorteile:** Datenträger kann wiederverwendet werden; keine spezielle Hardware erforderlich; kostengünstig
- **Nachteile:** Bei SSDs/Flash nicht alle Zellen erreichbar; abhängig von korrekter Software-Implementierung; Datenträger muss funktionsfähig sein
- **Abdeckung:** Sicherheitsstufen H-1 bis H-4 (für HDDs)

(Method: Overwriting data on the carrier with new data. Tools: Software like StickShredder. Advantages: Carrier can be reused; no special hardware required; cost-effective. Disadvantages: Not all cells reachable on SSDs/flash; depends on correct software implementation; carrier must be functional. Coverage: Security levels H-1 to H-4 for HDDs.)

### Physische Vernichtung (Physical Destruction)

- **Methode:** Mechanische Zerkleinerung, Entmagnetisierung (Degausser) oder thermische Vernichtung des Datenträgers
- **Werkzeuge:** Industrieschredder (DIN-zertifiziert), Degausser, Hochöfen
- **Vorteile:** Garantierte Vernichtung aller Daten unabhängig von Datenträgertyp oder -zustand; auch für defekte Datenträger geeignet
- **Nachteile:** Datenträger ist danach nicht wiederverwendbar; erfordert spezielle Ausrüstung oder externen Dienstleister; höhere Kosten
- **Abdeckung:** Sicherheitsstufen H-1 bis H-7

(Method: Mechanical shredding, degaussing, or thermal destruction. Tools: Industrial shredders (DIN-certified), degaussers, furnaces. Advantages: Guaranteed destruction regardless of carrier type or condition; suitable for defective carriers. Disadvantages: Carrier not reusable; requires special equipment or external service provider; higher costs. Coverage: Security levels H-1 to H-7.)

---

## Wann ist physische Vernichtung erforderlich?

**(When Is Physical Destruction Required?)**

Physische Vernichtung wird empfohlen oder ist erforderlich in folgenden Fällen:

(Physical destruction is recommended or required in the following cases.)

1. **Schutzklasse 3 (Sehr hoher Schutzbedarf):** Die Mindest-Sicherheitsstufe H-5 erfordert physische Vernichtung. Softwarebasiertes Überschreiben ist *nicht* ausreichend.

   (Protection Class 3: The minimum security level H-5 requires physical destruction. Software-based overwriting is *not* sufficient.)

2. **SSD- und Flash-Datenträger mit sensiblen Daten:** Aufgrund von Wear Leveling und Over-Provisioning kann softwarebasiertes Überschreiben nicht alle physischen Zellen erreichen. Bei Schutzklasse 2 und höher auf SSDs/Flash ist physische Vernichtung oder ATA Secure Erase dringend empfohlen.

   (SSDs and flash carriers with sensitive data: Due to wear leveling and over-provisioning, software-based overwriting cannot reach all physical cells. For protection class 2 and higher on SSDs/flash, physical destruction or ATA Secure Erase is strongly recommended.)

3. **Defekte Datenträger:** Wenn ein Datenträger nicht mehr funktionsfähig ist und nicht überschrieben werden kann, ist physische Vernichtung die einzige Option.

   (Defective carriers: If a carrier is no longer functional and cannot be overwritten, physical destruction is the only option.)

4. **Regulatorische Anforderungen:** Wenn Gesetze, Verträge oder Branchenstandards ausdrücklich physische Vernichtung vorschreiben.

   (Regulatory requirements: If laws, contracts, or industry standards explicitly require physical destruction.)

5. **Im Zweifelsfall:** Wenn Sie unsicher sind, ob logische Löschung ausreichend ist, wählen Sie die sicherere Option: physische Vernichtung.

   (When in doubt: If you are unsure whether logical deletion is sufficient, choose the safer option: physical destruction.)

> **Tipp (Tip):** Viele zertifizierte Aktenvernichtungsunternehmen bieten auch die Vernichtung digitaler Datenträger an. Achten Sie auf die DIN 66399-Zertifizierung des Dienstleisters und lassen Sie sich einen Vernichtungsnachweis ausstellen.
>
> (Many certified document destruction companies also offer destruction of digital data carriers. Look for the DIN 66399 certification of the service provider and obtain a destruction certificate.)

---

## Referenzen (References)

- **DIN 66399-1:2012-10** -- Vernichtung von Datenträgern -- Teil 1: Grundlagen und Begriffe
  (Destruction of data carriers -- Part 1: Principles and definitions)

- **DIN 66399-2:2012-10** -- Vernichtung von Datenträgern -- Teil 2: Anforderungen an Maschinen zur Vernichtung von Datenträgern
  (Destruction of data carriers -- Part 2: Requirements for machines for the destruction of data carriers)

- **DIN 66399-3:2013-02** -- Vernichtung von Datenträgern -- Teil 3: Prozess der Datenträgervernichtung
  (Destruction of data carriers -- Part 3: Process of data carrier destruction)

- **ISO 21964-1:2018** -- Internationalisierte Fassung der DIN 66399-1
  (International version of DIN 66399-1)

- **BSI -- IT-Grundschutz-Kompendium** -- CON.6: Löschen und Vernichten
  Bundesamt für Sicherheit in der Informationstechnik
  https://www.bsi.bund.de/grundschutz

- **BSI -- Richtlinie VSITR** -- Verschlusssachenanweisung des Bundes, Anlage zum materiellen Geheimschutz
  (BSI VSITR guideline -- Federal classified information directive)

- **DSGVO / GDPR** -- Verordnung (EU) 2016/679, insbesondere Artikel 5 (Grundsätze der Datenverarbeitung) und Artikel 17 (Recht auf Löschung)
  (Regulation (EU) 2016/679, in particular Article 5 -- Principles of data processing -- and Article 17 -- Right to erasure)

---

*Dieses Dokument dient ausschließlich der Information und stellt keine Rechtsberatung dar. Die Verantwortung für die korrekte Umsetzung der DIN 66399 liegt beim Anwender. Konsultieren Sie bei Bedarf Ihren Datenschutzbeauftragten oder einen Fachberater.*

*(This document is for informational purposes only and does not constitute legal advice. The responsibility for the correct implementation of DIN 66399 lies with the user. Consult your data protection officer or a specialist advisor if needed.)*
