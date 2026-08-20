# Brainstorm: Wiederverwendung vorhandener DefinedTerms

Bezug: Issue [#78](https://github.com/redlink-gmbh/ogd-to-lod/issues/78) — "Wiederverwendung vorhandener DefinedTerms in neuem RDF-Datenset"

## Ziel

Wiederverwendung vorhandener `schema:DefinedTerm`s in einem neuen RDF-Datenset, statt für jede
CSV-Spalte neue Code-Ressourcen zu erzeugen.

Dazu:

1. Herausfinden, ob in einem vorhandenen Datensatz (RDF-Wissensgraph, erreichbar über SPARQL-Endpoint)
   bereits Terme (`schema:DefinedTerm`) vorhanden sind, die in einem gegebenen tabellarischen
   Datensatz (CSV) vorkommen.
2. Herausfinden, wie die gefundenen DefinedTerms auf den neuen Datensatz (CSV-Zeile) gemappt
   werden können.

**Ausgabe:** Relation von Spalte → Mapping.

---

## Teil 1: Term Matching

### Ablauf

1. Aus dem Datensatz werden alle Spalten geholt, die **Key-Dimensions** sind.
2. Pro Key-Dimension wird über **n zufällige, unterschiedliche Werte** iteriert.
3. Pro Wert werden DefinedTerms im Datenset (SPARQL-Endpoint) gesucht.
   Ergebnis: eine Liste von Treffer-Terms + eine Liste von Termen, die keine Treffer liefern (evtl. leer)
4. Für jeden gefundenen Term wird ermittelt, zu welchem `schema:DefinedTermSet` er gehört.
   → Liste von TermSets.
5. Aus dieser Liste wird bestimmt, in welchem TermSet die **meisten**, der vorher ermittelten Terms
   liegen (Gewinner-TermSet pro Spalte). Prozentsatz ist konfigurierbar.
6. Aus diesem TermSet werden **alle** zugehörigen Terms extrahiert.
7. Diese Terms werden mit **allen Zeilen** des Datensatzes verglichen; es wird ein Prozentsatz
   berechnet, wie viele Zeilen/Werte der Spalte mit den Terms matchen.
8. Ab einem gewissen Prozentsatz **x %** wird ein Mapping vorgeschlagen.
9. Diese Schritte werden für alle Key-Dimension-Spalten wiederholt.

Wichtig: Schritt 2–5 arbeiten auf einer **Stichprobe** (billig, wenige SPARQL-Queries), Schritt 6–7
auf der **Vollmenge** (ein Query pro Kandidaten-TermSet, Vergleich lokal in Python). Damit bleibt die
Anzahl der Endpoint-Roundtrips unabhängig von der CSV-Grösse.

### Mapping-Vorschlag via LLM

Beim Mapping soll dem LLM ein Kontext gegeben werden, in dem an den Datensatz eine **zusätzliche
Spalte** angehängt wird, in die die URI des dazugehörigen Terms geschrieben wird. Anhand dieser
(angereicherten) Tabelle soll das LLM ein Mapping vorschlagen.

Beispiel des LLM-Kontexts:

| Jahr | Quartier   | Quartier_uri                                              | Anzahl |
|------|------------|-----------------------------------------------------------|--------|
| 2023 | Altstetten | https://ld.stadt-zuerich.ch/statistics/code/Q_Altstetten  | 1234   |
| 2023 | Wipkingen  | https://ld.stadt-zuerich.ch/statistics/code/Q_Wipkingen   | 987    |
| 2023 | Enge       | *(kein Treffer)*                                          | 456    |

=> Mapping wäre zB `'https://ld.stadt-zuerich.ch/statistics/code/Q' + $SPALTE_Quarier`

### Nicht vorkommende Terms

Für Terme, die im Datensatz vorkommen aber nicht gemappt werden können, soll
festgehalten werden, dass sie fehlen. Anschliessend Abfrage an das LLM, ob das Mapping so übernommen werden soll. GGF angleichen durch den Benutzer.

Fälle, die dahinterstehen (zu unterscheiden):

- TermSet ist breiter als der Datensatz (z. B. alle Quartiere vs. nur eine Auswahl) → unkritisch,
  Terms werden schlicht nicht referenziert.
- Datensatz verwendet eine andere Granularität/Ebene der Hierarchie → TermSet passt evtl. nur
  teilweise, ggf. ist ein Eltern-/Kind-TermSet der bessere Match.
- Schreibweise/Sprache weicht ab → Matching-Problem, kein Datenproblem.

### SPARQL-Queries

Im Issue ist an dieser Stelle nur "Query:" ohne Inhalt notiert. Vorschlag (Basis: bestehender Code in
`src/ogd_to_lod/lookup/sparql_client.py`):

**Q1 — Terms zu einer Menge von Werten finden (Stichprobe, pro Spalte):**

```sparql
PREFIX schema: <http://schema.org/>

SELECT ?term ?name ?termSet WHERE {
  ?term a schema:DefinedTerm ;
        schema:name ?name .
  OPTIONAL { ?term schema:isPartOf ?termSet }
  FILTER(STR(?name) IN ("Altstetten", "Wipkingen", "Enge"))
}
```

`FILTER(STR(?name) IN (...))` statt `VALUES ?name { ... }`: `VALUES` mit Plain-Literalen matcht keine
sprachmarkierten Literale (`"Altstetten"@de`). Der bestehende Code verwendet `VALUES` und würde bei
sprachmarkierten Namen leer zurückkommen — siehe "Ist-Zustand".

**Q2 — alle Terms eines TermSets (Vollvergleich):**

```sparql
PREFIX schema: <http://schema.org/>

SELECT ?term ?name WHERE {
  ?term a schema:DefinedTerm ;
        schema:isPartOf <TERMSET_URI> ;
        schema:name ?name .
}
```

## Teil 2: Property Matching

1. Für jeden gefundenen Term `t` (aus dem TermSet-Match) im Graph nachschauen: welche Tripel gibt es
   der Form `?obs ?property t`? → ergibt eine Menge von Properties, die diesen Term als Wert
   verwenden.
2. Für jede so gefundene Property: Name/Label extrahieren und mit dem CSV-Spaltennamen bzw. mit der Feldbeschreibung `X` vergleichen
   (Vergleich durch das LLM).
3. Bei Übereinstimmung → Property-Match vorschlagen.

**Q3 — Properties, die die gefundenen Terms als Objekt verwenden:**

```sparql
PREFIX cube: <https://cube.link/>
PREFIX rdfs: <http://www.w3.org/2000/01/rdf-schema#>
PREFIX schema: <http://schema.org/>

SELECT ?property ?label (COUNT(DISTINCT ?obs) AS ?usageCount) WHERE {
  ?obs a cube:Observation ;
       ?property ?term .
  VALUES ?term { <term1> <term2> <term3> }
  OPTIONAL { ?property rdfs:label|schema:name ?label }
}
GROUP BY ?property ?label
ORDER BY DESC(?usageCount)
```

`usageCount` dient als Tie-Breaker, wenn mehrere Properties denselben Term als Wert führen. Fehlt ein
Label, wird der Local Name der URI als Vergleichsgrundlage genommen.

## Ausgabe-Struktur

Pro Key-Dimension-Spalte:

```
column:            "Quartier"
term_set:          <https://ld.stadt-zuerich.ch/statistics/termset/quartier>
coverage:          0.94              # Anteil der Zeilen, deren Wert einen Term trifft
mapping: "'https://ld.stadt-zuerich.ch/statistics/code/Q' + $SPALTE_Quarier"
value_to_term:     { "Altstetten": <.../Q_Altstetten>, ... } # just a small subset
unmatched_values:  ["Enge"]          # im CSV, aber nicht im TermSet
property:          <https://ld.stadt-zuerich.ch/statistics/property/quartier>
```

Diese Struktur ist die Erweiterung von `MatchedDefinedTermSet` / `MatchedProperty` in
`src/ogd_to_lod/lookup/reuse_context.py`.

---
