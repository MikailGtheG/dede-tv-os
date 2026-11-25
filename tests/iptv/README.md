# Lokale IPTV-Testquelle

Synthetische Live-TV-Quelle für Integrations- und QEMU-Tests. Sie wird **nie** ins Appliance-Image installiert.

Die Senderliste entspricht der Produktvision:

| Nr. | Sender | Quelle |
|---|---|---|
| 01 | TRT 1 | IPTV-Test |
| 02 | ATV | IPTV-Test |
| 03 | Kanal D | IPTV-Test |
| 04 | Show TV | IPTV-Test |
| 05 | Dede Film | reserviert für den virtuellen Sender aus `dede-tv-core` |
| 06 | Teşkilat TV | IPTV-Test |
| 90 | Test Offline | Fehlerkanal: HTTP 404 (nur in `dede-test-faults.m3u`) |
| 91 | Test Stillstand | Fehlerkanal: Playlist läuft nicht weiter (nur in `dede-test-faults.m3u`) |

Der Inhalt ist ein generiertes Testbild: Hintergrundfarbe je Sender, Sendernummer und -name, laufende Zeit und ein Sinuston je Sender (440–659 Hz). Die Namen sind reine Testbezeichnungen.

## Verwendung

```sh
python3 tests/iptv/render_test_streams.py /tmp/iptv-media      # ca. 10 s, 5,3 MB
python3 tests/iptv/serve_test_iptv.py /tmp/iptv-media \
    --bind 0.0.0.0 --port 8089 --base-url http://10.0.2.2:8089  # QEMU-Gast sieht den Host als 10.0.2.2
```

Endpunkte: `/dede-test.m3u`, `/dede-test-faults.m3u`, `/epg.xml`, `/live/<id>/index.m3u8`.

Pro Sender gibt es eine Schleife aus 10 × 2 s, einmalig gerendert. Die Media-Sequence ergibt sich aus der Uhrzeit, sodass ein Sender auch ohne Zuschauer weiterläuft. Schleifen-Umbrüche sind mit `EXT-X-DISCONTINUITY` markiert. Neben QEMU muss daher kein Encoder laufen.

Lokal geprüft: 26 s Live-Wiedergabe über einen Umbruch, 650 Frames bei 25 fps, 0 Decodierfehler.
