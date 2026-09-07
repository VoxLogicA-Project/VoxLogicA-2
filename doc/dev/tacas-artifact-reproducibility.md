# Riproducibilità per l'artifact evaluation TACAS

Documento di lavoro, aggiornato mano a mano che i punti si chiudono. Le misure
numeriche (spazio disco, tempi) vengono da fmt-5000 e valgono come ordine di
grandezza, non come valori attesi: quelli sono il punto 2, ancora aperto.

Aperto il 2026-09-07 sul branch `fix/disk-reserve-collapse`.

## Come è stata scritta

Non è una checklist generica di buone pratiche. Ogni voce qui sotto è un modo
concreto in cui un revisore ostile può far fallire l'artifact, verificato sul
repo così com'è oggi. Il criterio è quello dell'artifact evaluation: il revisore
scarica lo Zenodo, si procura BraTS per conto suo (per policy non possiamo
distribuirlo), lancia quello che gli diciamo di lanciare, e deve ritrovare i
numeri del paper. Ogni punto dice **cosa vede il revisore**, non cosa è
tecnicamente vero.

## Riepilogo

| # | Criticità | Gravità | Stato |
|---|---|---|---|
| 1 | Programmi che escono 0 senza calcolare niente | **bloccante** | **fatto** (motore) / da fare (i 6 programmi) |
| 2 | Nessun oracolo: nessun valore atteso tracciato | **bloccante** | da fare |
| 3 | Percorsi dataset assoluti dentro i programmi | alta | da fare |
| 4 | L'artifact non si ricostruisce: niente lockfile, pin aperti | alta | **fatto** (`cbb8cdd`) |
| 5 | Nessun manifest del dataset | media | da fare |
| 6 | Test il cui esito dipende dallo spazio libero in `/tmp` | **alta** | **fatto**, branch `fix/disk-reserve-collapse` |
| — | Determinismo del motore parallelo | — | **verificato, assolto** |

---

## 1. Programmi che escono 0 senza calcolare niente — BLOCCANTE

**Cosa vede il revisore.** Lancia il programma. Exit code 0. Nessun errore.
Conclude che ha funzionato. Non è stato calcolato niente.

**Perché succede.** La valutazione è demand-driven: si calcola solo ciò che un
goal (`print` o `save`) richiede. Un programma senza goal costruisce il grafo,
non chiede niente a nessuno, ed esce 0 in una frazione di secondo.

**Estensione.** Sei programmi con zero goal:

| file | righe | anche percorso rotto? |
|---|---|---|
| `doc/gallery/programs/review/tropical_slices.imgql` | 94 | — |
| `doc/gallery/programs/simpleitk/sitk-brats-fixed-segmentation.imgql` | 25 | sì |
| `doc/gallery/programs/simpleitk/sitk-threshold-sweep-overlay.imgql` | 30 | sì |
| `tests/brats_best_axial_slice.imgql` | 65 | — |
| `tests/brats_flair_mean_threshold.imgql` | 43 | — |
| `tests/single_vi_case.imgql` | 25 | — |

`tests/brats_best_axial_slice.imgql` è il caso peggiore da leggere: contiene
`save_best_slice(...)` e `saved = ...`, che a occhio sembrano goal e non lo sono.

**Nota importante.** Questo punto *maschera* il punto 3. La primitiva `dir` già
solleva un errore se la radice non esiste (`dir.py:42`), quindi i tre programmi
con percorso rotto fallirebbero rumorosamente — ma solo se qualcuno chiedesse
quel valore. Senza goal nessuno lo chiede. Risolto il punto 1, il punto 3
diventa visibile da solo.

**Fix.** `voxlogica run` su un programma con zero goal non deve uscire 0 in
silenzio. Poi: dare un goal ai sei programmi, o ritirarli dalla gallery.

**Fatto il 2026-09-07** (`main.py`, `_run_command_inner`): se si chiede di
eseguire e il piano non ha goal, la CLI rifiuta con exit 2 invece di uscire 0.
La guardia è condizionata a `args.execute`, quindi `--no-execute` continua a
costruire e dumpare il grafo di un programma senza goal.

Verifica:

| programma | prima | dopo |
|---|---|---|
| `review/tropical_slices.imgql` | exit 0, 0,0 s | exit 2, "no goals" |
| `simpleitk/sitk-brats-fixed-segmentation.imgql` | exit 0 | exit 2, "no goals" |
| `simpleitk/sitk-threshold-sweep-overlay.imgql` | exit 0 | exit 2, "no goals" |
| `tests/brats_best_axial_slice.imgql` | exit 0 | exit 2, "no goals" |
| `tests/brats_flair_mean_threshold.imgql` | exit 0 | exit 2, "no goals" |
| `tests/single_vi_case.imgql` | exit 2 | exit 2 (*altro* motivo, vedi sotto) |
| `default/intro-hello.imgql` (con goal) | exit 0 | exit 0 |
| `tropical_slices.imgql --no-execute` | exit 0 | exit 0 |

**Scoperta collaterale.** `tests/single_vi_case.imgql` era già rotto per un
motivo indipendente: `E_UNBOUND_IDENTIFIER ... Unbound variable 'smoothen'` —
manca `import "vox1"`. Non era mai stato silenzioso, quindi non appartiene a
questa classe; va comunque riparato o ritirato.

**Conferma che il problema era reale.** Con `--no-execute --save-task-graph`,
il grafo di `tropical_slices.imgql` è **vuoto** (0 righe). Non è che calcolava
poco: non c'era proprio niente da calcolare.

**Resta da fare.** Decidere per ognuno dei sei: dargli un goal, o ritirarlo
dalla gallery. Tre sono citati in `doc/gallery/README.md` e
`doc/user/language-gallery.md`, quindi ritirarli tocca anche quelle tabelle.

---

## 2. Nessun oracolo — BLOCCANTE

**Cosa vede il revisore.** Ottiene dei numeri. Non ha nulla con cui
confrontarli, se non leggere il PDF del paper e confrontare a occhio.

**Estensione.** `git grep` per i nomi delle metriche (`dice_best_mean`,
`vi_thr_median`) nei file tracciati trova solo `doc/dev/replicating-tacas19-and-aiim.md`,
che dice **quali** numeri guardare ma non **quanto** devono valere. L'unico
documento con valori attesi è `doc/user/brats-tests-howto.md`, che è
gitignorato (`.gitignore:65`) e resta tale: contiene troppi riferimenti a
username e a questa macchina.

**Fix.** Un file tracciato con valori attesi e tolleranze, più uno script
`verify.sh` che confronta e ritorna diverso da zero se non tornano.

**Output atteso dopo il fix.** `./verify.sh` stampa una tabella
atteso/ottenuto/delta ed esce 0 solo se tutto sta nelle tolleranze.

---

## 3. Percorsi dataset assoluti dentro i programmi — ALTA

**Cosa vede il revisore.** Ha scaricato BraTS dove voleva lui. Deve aprire e
modificare N file sorgente prima di poter lanciare qualcosa.

**Estensione.** 11 file con `dataset_root = "/home/VoxLogicA/datasets/..."`:

```
doc/gallery/programs/simpleitk/brats-threshold-sweep-aiim.imgql:31
doc/gallery/programs/nnunet/brats-threshold-sweep-nnunet.imgql:28
tests/brats_brain_tumour_segmentation.imgql:13
tests/brats_best_axial_slice.imgql:8
tests/brats_flair_mean_threshold.imgql:4
tests/threshold_sweep.imgql:3
tests/differential/programs/tacas19.vl2.imgql:8
tests/differential/programs/tacas19.vl2main.imgql:8
tests/perf/scaling/abcd_comparison/bench_tacas19_incoming.imgql:5
tests/perf/scaling/abcd_comparison/bench_tacas19_main.imgql:5
tests/brats_segmentation_with_for.imgql:13   (BraTS_2019_HGG, non 2020)
```

3 file puntano a `tests/data/datasets/BraTS_2019_HGG`, **directory che non
esiste** in questo checkout:

```
doc/gallery/programs/simpleitk/sitk-brats-fixed-segmentation.imgql:3
doc/gallery/programs/simpleitk/sitk-threshold-sweep-overlay.imgql:3
```
(più `tests/brats_segmentation_with_for.imgql`, che usa la variante assoluta)

`doc/dev/replicating-tacas19-and-aiim.md` dice che
`tests/data/datasets/BraTS_2019_HGG` deve essere un symlink, ma non c'è nulla
che lo verifichi né che lo dica al revisore al momento giusto.

**Fix.** Il percorso viene da una variabile d'ambiente o da un file di
configurazione, letto una volta sola, non riscritto in ogni programma.

---

## 4. L'artifact non si ricostruisce — ALTA

**Cosa vede il revisore.** `bootstrap.py` scarica pacchetti da internet fra sei
mesi. Ottiene versioni diverse dalle nostre.

**Estensione.**

| problema | evidenza |
|---|---|
| nessun lockfile | non esistono `uv.lock`, `poetry.lock`, `requirements.lock` |
| `uv` non pinnato | `bootstrap.py:231` usa `latest/download` se `VOXLOGICA_UV_VERSION` non è settata |
| SimpleITK con pin aperto | `requirements.txt:23` → `SimpleITK>=2.5.5` |
| numba con pin aperto | `requirements.txt:22` → `numba>=0.66.0` |
| pywebview con pin aperto | `requirements.txt:15` → `pywebview>=6.2` |
| CPython free-threaded scaricato dalla rete | `.python-version` → `3.14t` |

SimpleITK è il pin che conta davvero per i numeri: è la libreria che fa
threshold, smoothing e connected components. Un minor bump può muovere l'ultima
cifra del Dice. È anche l'unico dei tre che entra nel percorso di calcolo.

**Fatto il 2026-09-07**, commit `cbb8cdd`.

| | prima | dopo |
|---|---|---|
| SimpleITK | `>=2.5.5` | `==2.5.6` |
| numba | `>=0.66.0` | `==0.67.0` |
| pywebview | `>=6.2` | `==6.2.1` |
| transitive | niente | `requirements.lock`, 148 pacchetti, 2682 hash |
| test | niente | `requirements-test.lock`, 161 pacchetti (superset) |
| uv | `latest/download` | `DEFAULT_UV_VERSION = "0.12.5"` |

Lock generati con `uv pip compile --universal --generate-hashes`: universale
perché un lock compilato su Linux non deve rompere il macOS di un revisore, con
hash perché un pacchetto ricaricato sotto la stessa versione deve far fallire
l'install invece di sostituirsi in silenzio. `tools/lock-requirements.sh` li
rigenera. `bootstrap.py` installa dai lock e dichiara quale strada ha preso.

Entrambi i lock viaggiano nell'artifact, e il README generato dice al revisore
quale riga controllare a fine bootstrap.

**Verifiche:** bootstrap sincronizza dai lock ed è idempotente; unit+contract
1187 passati, 0 falliti; AIIM su store vuoto 16/16 goal, 83,11 s, valori
**identici** al run di riferimento — il pinning non ha spostato niente.

---

## 5. Nessun manifest del dataset — MEDIA

**Cosa vede il revisore.** Si è procurato "BraTS 2020". Non ha modo di sapere
se ha *lo stesso* BraTS 2020 che abbiamo usato noi, con gli stessi casi nello
stesso ordine.

**Perché conta qui più del solito.** I programmi selezionano i casi per
*posizione*: `subsequence(dir(...), 0, case_count)` nell'AIIM, `train_start` /
`eval_start` nel programma nnU-Net. L'ordine viene da `sorted()` sui nomi file.
Un caso in più, un caso in meno, o una directory annidata diversamente, e il
revisore sta valutando un insieme di casi diverso ottenendo numeri diversi,
senza nessun segnale che qualcosa non torni.

**Fix.** Un manifest tracciato con: numero di casi atteso, nomi dei casi
attesi nell'ordine in cui `dir` li restituisce, e un hash per file. Più un
controllo che lo confronti prima di partire.

---

## 6. Test il cui esito dipende dallo spazio libero in `/tmp` — ALTA

Promosso da media ad alta: era "15 test rossi nascosti da `--maxfail=1`", ma
scavando è venuto fuori qualcosa di peggio.

**Cosa vede il revisore.** Lancia la suite sulla sua macchina e vede test rossi
che da noi sono verdi. Oppure, girandola due volte di fila sulla stessa
macchina, la vede rossa una volta e verde l'altra. È il tipo di cosa che chiude
un artifact evaluation.

**Cosa è successo qui.** Girando `tests/unit tests/contract` due volte a
distanza di minuti sullo stesso checkout:

| run | fallimenti |
|---|---|
| primo | 6 falliti, 1166 passati |
| secondo | 0 falliti, 1172 passati |

I sei: due in `test_cache_eviction.py`, tre in `test_eviction_leaves_a_reason.py`,
uno in `test_memory_backpressure.py`. Isolati passano sempre.

**Causa, verificata.** `SQLiteResultsDatabase._effective_max_bytes`
(`storage.py:765-806`) ricalcola il tetto dal disco:

```
reserve       = min(max(50 GB, total * 0.05), total // 2)
_disk_ceiling = max(0, _payload_bytes + free - reserve)
budget         = min(_max_bytes, _disk_ceiling)
```

Su `/tmp`, che qui è una tmpfs da 31 GB, `reserve` va a **15,5 GB** (il ramo
`total // 2`). Sotto quella soglia di spazio libero `_disk_ceiling` collassa a
0, il budget effettivo diventa 0, e `_enforce_budget` sfratta *tutto*. I test
scrivono in `tmp_path`, che sta su `/tmp`.

Misurato simulando `shutil.disk_usage` su un db con `max_bytes=5 MB`:

| `/tmp` libero | `_disk_ceiling` | budget effettivo | esito |
|---|---|---|---|
| 30 GB | 14,50 GB | 5.000.000 | verde |
| 20 GB | 4,50 GB | 5.000.000 | verde |
| 16 GB | 0,50 GB | 5.000.000 | verde |
| **15,4 GB** | **0** | **0** | **rosso: sfratta tutto** |
| 10 GB | 0 | 0 | rosso |
| 2 GB | 0 | 0 | rosso |

Il commento a `storage.py:786-793` documenta la metà precedente dello stesso
problema (tetto a zero letto come "nessun limite", cache cresciuta senza
freno). La correzione ha reso `_UNBOUNDED` un sentinella distinta, e adesso
zero significa "sfratta tutto" — che sui volumi piccoli è il caso normale, non
l'eccezione.

**Perché è un problema di riproducibilità e non solo di test.** La stessa
funzione governa lo store vero durante un esperimento. Un revisore con un
`/tmp` piccolo, o con `--store-db` su un volume quasi pieno, non ottiene un
errore: ottiene una cache che sfratta tutto e ricalcola sempre. Lento, ma
soprattutto silenzioso.

**Fatto il 2026-09-07**, branch `fix/disk-reserve-collapse` (`storage.py:790-812`):

```python
headroom = self._payload_bytes + usage.free
reserve  = min(max(50 GB, total * 0.05), headroom // 2)   # era: total // 2
self._disk_ceiling = headroom - reserve                    # era: max(0, ...)
```

Il `total // 2` misurava la cosa sbagliata: il tetto si calcola dallo spazio
*libero*, ma il clamp guardava la *dimensione del volume*, quindi su qualsiasi
volume meno che mezzo vuoto la reserve si mangiava l'headroom lo stesso.
Clampando su `headroom // 2` la regola dice quello che intende — non trattenere
mai più della metà di quello che c'è — e il tetto arriva a zero solo quando non
resta niente. Il `max(0, ...)` diventa superfluo e sparisce con lo stato
ambiguo che nascondeva.

Due correzioni della stessa famiglia: `statvfs` che fallisce senza budget
configurato dà `_UNBOUNDED` invece di 0; e quando il disco taglia il budget
richiesto ora c'è un `logger.warning`, una volta sola.

Il dirupo su `/home/laura` (store da 8,7 GB):

| libero | tetto prima | tetto dopo |
|---|---|---|
| 240 G | 65,4 G | 124,3 G |
| **174 G** | **0 G** | 91,3 G |
| 100 G | 0 G | 54,3 G |
| 20 G | 0 G | 14,3 G |
| 0 G | 0 G | 4,3 G |

**Test:** `tests/unit/test_disk_reserve_ceiling.py`, nuovo — la logica non era
coperta da niente, ed è il motivo per cui il bug è passato. 12 casi: verdi con
il fix, **8 rossi senza** (i 4 verdi sono i livelli sopra la soglia, dove il bug
non si manifestava).

**Verifiche:** suite `unit+contract+integration+regression` 1184 passati, 0
falliti. AIIM su store nuovo: exit 0, 16/16 goal, 15.356 operazioni, 99,17 s,
valori **identici** al run di riferimento.

**Sul flag.** `pytest.ini` ha `--maxfail=1`, quindi la suite si ferma al primo
rosso dopo ~8 test su ~1170. Va tolto o reso opzionale: nasconde esattamente
questa classe di problemi.

---

## Determinismo — VERIFICATO, ASSOLTO

Non è una criticità: è stato controllato ed è a posto. Resta qui perché è la
prima cosa che un revisore chiederebbe di un motore concorrente, free-threaded,
con sfratto e ricalcolo dei valori.

Programma: `doc/gallery/programs/simpleitk/brats-threshold-sweep-aiim.imgql`,
con `--no-cache` (nessun livello su disco, quindi ogni valore ricalcolato).

| thread | tempo | exit | goal |
|---|---|---|---|
| 1 | 167,75 s | 0 | 16/16 |
| 4 | 80,95 s | 0 | 16/16 |
| 16 | 55,51 s | 0 | 16/16 |

| confronto | esito |
|---|---|
| t=1 contro t=4 | identici |
| t=1 contro t=16 | identici |
| t=16 contro run con cache calda | identici |

Bit per bit, su tutti e 16 i goal, comprese le liste `best_dice` a 16 cifre
decimali. Il numero di thread cambia solo il tempo.

**Cosa non copre.** Una macchina sola (stessa CPU, stesso SimpleITK — vedi
punto 4); un programma solo; e nulla sul percorso GPU. Il programma nnU-Net
resta non riproducibile per costruzione (GPU, seed, cuDNN) a meno di congelare
e distribuire i pesi.

---

## Fuori perimetro, per decisione

I dataset BraTS non vanno su Zenodo né altrove: è policy BraTS, i revisori se
li procurano da soli. Tutto quanto sopra assume questo vincolo.
