# Historische Eingangsrechnungen: Stammdatenluecke im MCP

Stand: 2026-09-10. Untersuchung und lokale Absicherung, kein Produktionsrollout.
Keine Lieferanten, Adressen, Bankkonten oder Rechnungen wurden korrigiert.

Update-Sicherung: Der vollstaendige Patch einschliesslich dieser Dokumentation
und Offline-Tests wird im privaten `server_management`-Repo unter
`ops/frappe-docker/resources/frappe_assistant_core` versioniert. Die dortige
Manifestdatei fixiert Upstream-Basis, Patch-Pruefsumme und erwarteten Git-Baum.
Ein neuer Upstream-Stand erfordert eine bewusste Aktualisierung und Abnahme.
Das ersetzt noch nicht die unten beschriebene Frappe-Staging-Abnahme.

## Nachgewiesener Fall

- Lieferant: Swisscom (Schweiz) AG.
- Rechnung: `ACC-PINV-2026-00040`, Rechnungsnummer `13243603-1`.
- Audit `ASST-AUDIT-2026-09-08-00140`, 15:13:44: `create_document`
  legt den Supplier nur mit Name, Typ, Gruppe, Waehrung und Steuer-ID an.
  Adresse und IBAN sind im uebergebenen Payload nicht enthalten.
- Audit `ASST-AUDIT-2026-09-08-00141`, 15:13:45: `create_document`
  erstellt die Purchase Invoice mit `submit=true`. Die erkannte QR-IBAN
  steht ausschliesslich im Bemerkungstext; `supplier_address` fehlt.
- Beide Aufrufe wurden von `frappe_assistant_core` als `Success` protokolliert.
- Originaldatei: `/private/files/Swisscom_Rechnung_13243603-181b0fa.pdf`.
  Seite 1 enthaelt die Lieferantenadresse; Seite 2 wiederholt Adresse und
  QR-IBAN im Zahlteil. Vollstaendige Kontodaten werden hier nicht dupliziert.
- Lesekontrolle am Untersuchungstag: keine mit diesem Supplier verknuepfte
  Address, keine Supplier-IBAN und kein supplier-eigenes Bank Account vorhanden.

Damit ist nicht nur eine Vermutung ueber OCR belegt: Die IBAN war dem damaligen
Aufruf bekannt, wurde aber im falschen Feld abgelegt. Die Fachaufgabe wurde
vorzeitig beendet, obwohl die einzelnen Schreiboperationen technisch erfolgreich
waren. Die Schnittstelle pruefte nur Frappe-Pflichtfelder, nicht die Vollstaendigkeit
des gesamten Rechnungseingangs.

## Abgrenzungen

- Ein leeres `supplier_primary_address` beweist keine fehlende Adresse:
  libracore und Torgen besitzen verknuepfte Address-Datensaetze.
- Bei `ACC-PINV-2026-00004` sind die ausgewaehlte Adresse und strukturierte
  Bankdaten vorhanden; der neue Check meldet `structurally_complete`.
- Bei Torgen fehlt in der Stichprobe nur die strukturierte Bankinformation,
  nicht die Adresse. Daraus folgt keine Aussage, ob die Originalrechnung eine
  IBAN enthielt oder eine andere Zahlungsart vorgesehen war.
- Viseca wurde nicht als weiterer belegter OCR-Fehler gewertet: Die Extraktion
  des angehaengten Scans scheiterte am fehlenden optionalen PaddleOCR-Backend.
- Die Aussage ueber den historischen Ablauf beruht auf den beiden Audit-Payloads;
  die Stammdatenkontrolle beschreibt den heutigen Zustand. Andere Imports und
  zwischenzeitliche manuelle Aenderungen wurden nicht pauschal zugeordnet.

## Implementierte Absicherung

Die Aenderung liegt im tatsaechlich verwendeten generischen Schreibweg von
Frappe Assistant Core, Basis `7ddc433` / v2.5.1, Branch
`codex/supplier-invoice-master-data`. `kt_codex_ops` und das CAD-Repo bleiben
unveraendert. Nur dessen neueren PDF-Preview zu verschaerfen haette den
historischen `create_document`-Weg nicht abgesichert.

1. Supplier-Anlage und -Aenderung liefern `master_data_review` mit stabilen
   Fehlercodes und konkreten Nacharbeiten. Ein Supplier ist ein echter
   Stammdatensatz, kein spaeter zu buchender Entwurf. Technischer Erfolg
   bedeutet nicht vollstaendigen Rechnungsimport (`import_complete=false`).
2. Bei Purchase Invoice werden Supplier, dynamisch verknuepfte Adressen und
   die tatsaechlich ausgewaehlte Rechnungsadresse geprueft. Leere Primaradress-
   Verweise werden nicht mit fehlenden Adressen verwechselt; falsche,
   deaktivierte und unvollstaendige Adressen bestehen die Pruefung nicht.
3. Die IBAN muss strukturiert in `Supplier.iban` oder einem aktiven,
   supplier-eigenen `Bank Account` liegen. Die bestehende Frappe-IBAN-Validierung
   wird verwendet. Eigene Firmenkonten oder Konten anderer Parteien zaehlen nicht.
4. Fehlende Leserechte, maskierte Daten, Abfragefehler und abgeschnittene
   Treffermengen werden als `unverifiable` statt als Erfolg ausgegeben.
5. `create_document(submit=true)` wird bei offenen Punkten vor `insert()`
   gestoppt. Derselbe Check gilt fuer `submit_document` und fuer direkte
   `docstatus=1`-Aenderungen im generischen Create-/Update-Weg. Ein bewusster
   Rechnungsentwurf bleibt moeglich und meldet die offenen Punkte.
6. Toolbeschreibungen und Benutzungshinweise benennen den Ablauf einschliesslich
   Zahlteil, strukturierter Ablage und abschliessendem Ruecklesen.

## Grenzen und Rollout

- Der Check ist strukturell. Er beweist weder die Identitaet des Zahlungsempfaengers
  noch die Uebereinstimmung mit einem PDF und erteilt keine Zahlungsfreigabe
  (`payment_authorized=false`). Geaenderte Bankdaten und QR-Referenzen brauchen
  weiterhin einen beleggestuetzten Vergleich und gegebenenfalls menschliche Freigabe.
- Bar-/Kartenbelege und Zahlungen ohne IBAN werden bei fehlender IBAN bewusst
  zur manuellen Pruefung verwiesen, nicht mit erfundenen Daten vervollstaendigt.
  Vor einem breiten Rollout muss dafuer ein nachvollziehbarer Ausnahmeprozess
  mit bestaetigter Quelle festgelegt werden; ein ungeprueftes Override-Boolean
  ist keine Freigabe.
- Ein neuer Supplier darf zunaechst unvollstaendig gespeichert werden, damit
  Address und Bank Account danach auf ihn verweisen koennen. Eine atomare,
  beleggebundene Supplier/Address/Bank/Invoice-Erfassung waere eine weitere
  Ausbaustufe. Die neue Antwort meldet die Zwischenstufe ausdruecklich als offen.
- Andere Schreibschnittstellen ausser den drei geaenderten FAC-Tools werden
  dadurch nicht global gesperrt. Der Schutz darf nicht ueber andere Tools umgangen
  werden. Ein produktweiter Zwang waere als ERP-Domainregel separat zu entwerfen.
- Die produktive Version wurde nicht ersetzt und kein Dienst neu gestartet.
  Der Patch muss vor Aktivierung in den verwalteten Build aufgenommen und in
  einer isolierten Frappe-Testsite samt Nicht-IBAN-Faellen abgenommen werden.

## Verifikation

- 31 Offline-Regressionstests mit anonymisierten Daten: historische Payloadform,
  verknuepfte Adresse ohne Primaerzeiger, falsche Parteien, deaktivierte Konten,
  maskierte Daten, fehlende Rechte, IBAN-Pruefung und alle drei Schreibwege.
- Aufruf: `py -3 -m pytest tests/test_supplier_master_data_unit.py -q -o addopts=`.
- Zusaetzlich wurde ausschliesslich die neue Lesefunktion im laufenden Frappe
  ausgefuehrt, ohne Module zu installieren: Site `erp.local`, Actor
  `pb@ktwaerme.ch`, `SET SESSION TRANSACTION READ ONLY`, abschliessender Rollback.
  Swisscom Supplier und Rechnung: `needs_review`; libracore-Rechnung:
  `structurally_complete`; Torgen: Bankdaten offen, Adresse erkannt.
- Kein Schreib-/Buchungstest gegen die Produktionsdatenbank. Die vollstaendige
  Bench-Testsuite wurde ohne lokale Frappe-Testsite nicht ausgefuehrt.
