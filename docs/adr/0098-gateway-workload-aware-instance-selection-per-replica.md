# 0098 — Gateway-Instanzauswahl workload-bewusst, aber bewusst nur pro Replika (nicht über Redis geteilt)

**Status:** akzeptiert
**Kontext:** Konzept 3.5, Session P25-S4 (`gateway-service`)

## Entscheidung

`InstanceResolver.pick()` wählt seit P25-S4 unter mehreren gesunden
Kandidaten eines `service_type` die Instanz mit den wenigsten aktuell
offenen Anfragen, statt wie zuvor (ADR 0005) rein zufällig
(`random.choice`). Der Zähler offener Anfragen (`dict[str, int]`, Schlüssel
= Instanz-Adresse) lebt **ausschließlich im Prozessspeicher der jeweiligen
`InstanceResolver`-Instanz** — bei mehreren horizontal skalierten
Gateway-Replikas hinter einem Load Balancer führt jede Replika ihren
eigenen, unabhängigen Zähler. Es gibt bewusst **keinen** clusterweit
geteilten Zähler (z. B. über Redis), obwohl genau dieses Muster erst eine
Session zuvor (P25-S3, ADR 0097) für den Rate Limiter eingeführt wurde.

Reserve/Release erfolgt über einen Async-Context-Manager
(`resolver.reserved_instance(instances)`), der `pick()` aufruft, den Zähler
vor dem Upstream-Aufruf erhöht und in einem `finally` wieder freigibt — auch
bei einer `httpx.HTTPError` während des Upstream-Aufrufs selbst. Tie-Break
bei mehreren Instanzen mit demselben Minimum: zufällig unter den
Minimum-Kandidaten (verhindert, dass im Ruhezustand, wenn alle Zähler bei 0
stehen, immer dieselbe erste Instanz der Liste bevorzugt würde).

## Begründung

Der Rate Limiter (ADR 0097) musste zwingend clusterweit geteilt werden: ein
rein lokaler Zähler wäre ein **umgehbares Sicherheitsversprechen** gewesen —
ein Client hätte das Limit durch Verteilung seiner Anfragen über mehrere
Gateway-Replikas faktisch vervielfachen können. Bei der Instanzauswahl fehlt
dieser Umgehungs-Anreiz vollständig: ein "zu gleichmäßig verteilter"
Load-Balancing-Zähler bringt einem Client keinen Vorteil, den er gezielt
ausnutzen könnte. Die Instanzauswahl ist eine reine Performance-/Fairness-
Heuristik, kein Zugriffsschutz.

Ein clusterweit geteilter Zähler über Redis wäre technisch möglich (analog
zu ADR 0097, z. B. `INCR`/`DECR` je Instanz-Adresse), hätte aber einen realen
Preis: **zwei zusätzliche Redis-Roundtrips pro proxiedtem Request** (einer
vor, einer nach dem eigentlichen Upstream-Aufruf, zusätzlich zum bereits
vorhandenen Rate-Limit-Roundtrip aus P25-S3) — auf dem heißesten Pfad des
gesamten Systems (praktisch jeder Request durchläuft `proxy()`). Dieser Preis
steht in keinem sinnvollen Verhältnis zum Nutzen: selbst eine rein
pro-Replika-lokale Sicht approximiert "wenig ausgelastete Instanz bevorzugen"
bereits gut genug, da jede Replika ohnehin nur einen Ausschnitt des
Gesamtverkehrs sieht und dieser Ausschnitt bei mehreren Replikas hinter einem
Load Balancer selbst schon einigermaßen gleichmäßig verteilt ist. Im
schlimmsten Fall führt die fehlende Cluster-Sicht zu einer etwas suboptimalen,
aber niemals sicherheitsrelevant falschen Verteilung.

## Konsequenzen

- Bei mehreren Gateway-Replikas ist die Lastverteilung pro Replika lokal
  optimal, global nur approximativ — eine einzelne, von außen "unglücklich"
  wirkende Instanzwahl über mehrere Replikas hinweg ist möglich, aber
  folgenlos (kein Sicherheitsproblem, nur eine geringfügig suboptimale
  Verteilung).
- Kein neuer Infrastruktur-Bedarf (kein zusätzlicher Redis-Zugriff) — anders
  als P25-S3 bringt diese Session keine neue Abhängigkeit oder Latenz auf den
  proxied Request-Pfad.
- Ein echter Wechsel zu einer clusterweiten Sicht bliebe später möglich
  (gleicher Redis-Dienst wie beim Rate Limiter wäre bereits vorhanden), ist
  aber nicht Teil dieser Entscheidung und aktuell nicht für nötig befunden.
- Weiterhin nicht latenz-bewusst (nur Anzahl offener Anfragen, keine
  tatsächliche Antwortzeit-Messung) — siehe "Offene Punkte" in
  `docs/services/gateway-service.md`.
