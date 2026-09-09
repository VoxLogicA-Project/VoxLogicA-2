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

Aggiornato 2026-09-08. La numerazione è quella originale, così i riferimenti
nei commit continuano a valere; i punti 7 e 8 sono nuovi e non erano nella
lista di partenza — sono venuti fuori facendo il resto.

| # | Criticità | Gravità | Stato |
|---|---|---|---|
| **7a** | Doppio dispatch: `DoubleComputationError` | bloccante | **fatto da Vincenzo** (`a313172`) |
| **7b** | **Rematerializzazione: `NeedsExpansion` a store caldo** | **bloccante** | **aperto** — una causa chiusa (`29634eb`), il fallimento resta |
| 1 | Programmi che escono 0 senza calcolare niente | bloccante | fatto (motore) / 6 programmi da sistemare |
| 2 | Nessun oracolo: nessun valore atteso tracciato | bloccante | **fatto per 2 e 3, provvisorio per 4** |
| 3 | Percorsi dataset assoluti dentro i programmi | alta | mitigato nell'artifact, aperto nel repo |
| 4 | L'artifact non si ricostruisce: niente lockfile, pin aperti | alta | fatto (`cbb8cdd`) |
| 5 | Nessun manifest del dataset | media | **fatto** |
| 6 | Test il cui esito dipende dallo spazio libero in `/tmp` | alta | fatto (`ad55306`) |
| **8** | **Lavoro costoso perso per un errore a valle** | media | causa immediata fatta (`b0b68a1`), architettura aperta |
| — | Determinismo dei valori | — | verificato, con una riserva (vedi 7) |

Gli aperti sono di natura diversa. Il **2** e il **5** non sono patch: sono
decisioni su quali numeri dichiarare e quale dataset dichiarare, e le prendi tu.

## Cosa esiste adesso che prima non c'era

`tools/make_artifact.py` (`596a7a4`, `6a0459a`) costruisce la cartella
dell'artifact dal checkout, in un secondo, tutte le volte che serve. Quattro
liste in cima — `ENGINE`, `PROGRAMS`, `MODEL`, `EXCLUDE` — sono l'intera
configurazione; il README del revisore è generato da quelle e dai file
copiati, quindi non può divergere.

I quattro esperimenti spediti:

| # | esperimento | serve | tempo | goal | riproducibile |
|---|---|---|---|---|---|
| 1 | smoke test (cerchi sintetici) | niente | 15-25 min | 1 | pass/fail |
| 2 | ricetta TACAS, cinque casi | BraTS2020 | 2-5 min | 15 | bit-identical |
| 3 | sweep AIIM | BraTS2020 | 60-170 min | 16 | bit-identical |
| 4 | sweep AIIM su riferimento appreso | BraTS2020 + GPU | ore | 23 | no, allena una rete |

Lo script rifiuta di spedire dati BraTS, rifiuta pesi che nessun programma
spedito può usare, e si ferma se un programma non ha goal.

## Il modello preaddestrato, e cosa costa davvero

Allenato il 2026-09-07: 1000 epoche, 50 casi, `3d_fullres`, ~13 ore.
Validation Dice **0,8856**, che il postprocessing porta a **0,8865**. I pesi
stanno in `/home/laura/nnunet-brats-work`, che è dove `MODEL` li cerca.

**I pesi risparmiano il training, non il dataset.** In
`runtime.py::train_model`, `nnUNetv2_plan_and_preprocess` gira
incondizionatamente *prima* del controllo per-fold "checkpoint già esistente",
quindi i casi grezzi vengono comunque materializzati e preprocessati. Senza
BraTS l'esperimento 4 non parte, pesi o non pesi. È scritto nel README del
revisore invece di essere lasciato intendere.

## Il risultato dell'esperimento 4, primo run completo

Fuori campione, casi 50-69, che la rete non ha mai visto:

| metrica | valore |
|---|---|
| `model_dice_mean` | 0,8588 |
| `model_dice_median` | 0,9152 |
| `model_dice_stdev` | 0,1101 |
| `dice_fixed_mean` (soglia pubblicata) | 0,8069 |
| `dice_best_mean` | 0,8502 |
| `vi_thr_median` | 0,90 |

```
vi_thr_distribution = [[0.73,1], [0.87,2], [0.89,4], [0.90,4], [0.91,2], [0.92,7]]
```

**La dispersione sopravvive.** Sei soglie diverse vincono su venti casi. La
case-dependence di `viThr` non era un artefatto dell'annotatore: resta quando
il riferimento diventa la predizione di una rete.

Da leggere con la riserva che i numeri stessi impongono: `model_dice_stdev` è
0,11 con un caso a 0,54, quindi su alcuni casi il riferimento appreso è debole
e lì la soglia "migliore" concorda con un modello mediocre. Mediana 0,915
contro media 0,859 dice esattamente questo.

---

---

## 7a. Doppio dispatch — CHIUSO da Vincenzo il 2026-09-08

Segnalato da qui come fallimento non deterministico dello sweep nnU-Net:

```
DoubleComputationError: node <id> already running
NodeExecutionError: simpleitk.ReadImage failed while evaluating node <id>
```

Le tre proprietà che avevamo raccolto — la vittima cambia a ogni run, è sempre
`ReadImage`, sparisce a `--threads 1` — sono quelle che hanno nominato la causa.
Chiuso in `a313172`.

**La causa.** Fra il pop del nodo e `table.begin` non c'è nessun `await`, quindi
due coroutine non possono interlacciarsi lì: lo stesso id era stato **offerto**
due volte. `_await_named_deps` e `_await_expansion` proteggono la registrazione
ma lasciano scoperta la push, e `graph.incomplete` significa
*registrato-e-non-finito*, che include un nodo che un worker sta eseguendo in
quel momento.

**Perché l'AIIM non falliva mai**, che era la domanda che gli avevamo indicato
come traccia migliore: `_await_named_deps` si entra solo per una dipendenza il
cui valore contiene handle, cioè una sequenza costruita pigramente. Nel
programma nnU-Net `modalities_of(i)` è un letterale di quattro `ReadImage`
passato a `nnunet.predict`, che è eager, e quei ref sono condivisi con
`pflair_of(i)`, quindi spesso già in volo. Nell'AIIM `ReadImage` è un arco
ordinario — `index(flair_paths, i)` produce una stringa, non un handle — e quel
percorso non viene mai preso.

**La scelta di progetto** è quella che avevamo posto come domanda aperta: se
l'invariante sia "non deve succedere" o "non deve costare". La risposta è la
seconda. La ready queue è un suggerimento, non proprietà; `_worker` chiede
`table.is_running` prima di reclamare e un'offerta duplicata costa un pop
sprecato. `begin` continua a sollevare: l'invariante non cambia, non ci si
arriva più per un duplicato benigno.

---

## 7b. Rematerializzazione: `NeedsExpansion` a store caldo — ANCORA APERTO

**Aggiornamento del 2026-09-09.** `29634eb` ha chiuso *una* causa, non il
fallimento. L'esperimento 4 rilanciato sullo store caldo la notte del 2026-09-08
muore ancora, sullo stesso nodo:

```
[stuck] qsize=0 outstanding=59 completed=1017 stuck=0 alias=1 jobs=0
NeedsExpansion: 9434d297... must be expanded, not computed     exit=70, 21/23 goal
```

Quello che il fix ha effettivamente prodotto, e che regge: l'AIIM sullo store
caldo dà exit 0 in 36 s con i sei valori bit-identici all'oracolo, e i tre
invarianti sono pinnati da `tests/unit/test_warm_loop_node_is_still_expandable.py`.
Quello che avevo scritto in più — che con la correzione nessuno sarebbe più
arrivato al percorso dei goal — era un'inferenza dall'esperimento 2, non una
misura sul 4, ed è falsa.

**La traccia completa, che il 2026-09-08 non avevamo** (`voxlogica errors show
VLX-AC2EDB94`):

```
strategy.py:318 run → :410 _side_effect → :435 _materialize
  → handles.py:95 resolve_deep → :132 _rebuild
  → core.py:1314 _resolve_reference → :1530 _rematerialize
     preceduto da CINQUE frame annidati di core.py:1551 _rematerialize(child)
```

Tre elementi nuovi, che spostano la diagnosi:

1. Il nodo loop **non è il bersaglio dell'handle**: è una dipendenza cinque
   livelli sotto, raggiunta ricostruendo lo scaffolding di un altro valore. Non
   è il caso che la mancata potatura di `_available` copre.
2. Succede a **motore già spento**, dentro `_side_effect`, che sta fuori dal
   `try/except` che protegge `query.result()`.
3. Il run **si era già impiantato prima**: `outstanding=59` con `qsize=0`,
   `jobs=0` e `stuck=0` — cioè il dump della frontiera non nomina nessun nodo in
   attesa. Lo stallo è il fallimento primo; il `NeedsExpansion` è quello che si
   vede.

Da qui riparte l'indagine: perché 59 unità restano in sospeso senza che nessun
nodo risulti in attesa. E, di passaggio, `core.py:1551` mostra che
`_rematerialize` ricorre sulle dipendenze, cosa che AGENTS.md vieta.

---

### Storia: la prima causa, chiusa il 2026-09-08 in `29634eb`

Non era l'altra faccia del 7a: sopravviveva alla sua correzione. Era un bug
indipendente, mascherato prima del merge dal doppio dispatch.

```
[stuck] qsize=0 outstanding=49 completed=1485 stuck=0 alias=10 jobs=0
NeedsExpansion: 9434d297... must be expanded, not computed
```

**I sei run.**

| run | store | thread | esito |
|---|---|---|---|
| 09:28 | popolato | 24 | `DoubleComputationError` |
| 10:34 | popolato | 24 | `DoubleComputationError` x3 |
| 10:36 | quasi freddo | 1 | 23/23 |
| ~13:00 | caldo | 1 | `NeedsExpansion`, 17/23, un `for_loop` con `pending=0 unmet=[]` |
| 14:2x | **vuoto** | 1 | 23/23 |
| post-merge | caldo | 24 | `NeedsExpansion`, **21/23**, `stuck=0` |

**La causa, misurata sullo store e non ipotizzata.** L'ipotesi che avevo scritto
— sfratto di un valore creduto durevole, scrittura non atterrata — era
sbagliata. Interrogando `~/.voxlogica/results.db` sul nodo della traccia:

| campo | valore |
|---|---|
| `metadata_json` | `{"operator":"default.for_loop"}` |
| `status` | `materialized` |
| `payload_json` | `sequence-json-v1`, 69 handle |
| i 69 elementi | 69 su 69 presenti, tutti `materialized` |
| righe `evicted` nell'intero store | **zero** |

Niente era andato perso. Lo sfratto su disco non aveva mai girato — e lascia
tombstone permanenti, quindi la loro assenza è una prova, non un indizio.

Il valore era intatto e **il motore lo rifiutava**. Un nodo loop persiste un
container che nomina i propri body per hash, e quegli id esistono solo dopo
l'espansione; `NodeTable.load` esige (`_references_are_answerable`) che siano
internati in *questo* run e quindi restituisce `None`. Ma su un run caldo
`_available` aveva potato il nodo loop proprio perché `persisted()` era vero:
promessa che lo store non può mantenere. `_rematerialize` legge quel `None` come
"non c'è", non trova alias e solleva `NeedsExpansion`.

Verificato sul valore reale, con la guardia vera:

| stato del run | guardia | `load()` |
|---|---|---|
| loop espanso (69 body internati) | `True` | restituisce il valore |
| loop **potato** perché `persisted` | **`False`** | **`None`** |

È deterministico, non una race: su store caldo è garantito. Spiega la tabella
meglio dell'ipotesi precedente — store vuoto passa sempre perché il loop si
espande e i body si internano.

**Il fix**, in `engine/core.py`:

1. **Un nodo che fa crescere il grafo non è mai "disponibile da disco"**
   (`_available` e la copia inline in `_schedule_subgraph`). La potatura non
   vale la pena difenderla: lo scheduling arriva a quel nodo solo perché un
   consumatore sopra di lui va ricalcolato, e quel consumatore ne chiederà il
   valore. Riespandere costa la lista degli hash; gli elementi continuano a
   venire dal disco.
2. **L'inoltro loop→sequence sopravvive al turno del worker.** `_alias` viene
   consumato quando il loop inoltra, e da lì in poi il loop non sapeva più da
   dove veniva il suo valore — mentre la sequence spliced teneva sul disco lo
   stesso payload (`for_loop 9434d297` e `sequence bcf0c3d3`, righe identiche).
   La risoluzione ora legge `_forward`, che dura quanto il run.
3. **Un waiter non viene mai parcheggiato su un loop già completato.**
   `_await_expansion` faceva `register` prima di `await_one`, quindi rimetteva
   il nodo in `incomplete` e il wait veniva concesso su un arrivo già
   annunciato: coda vuota che non drena mai (`qsize=0 outstanding=49 stuck=0`).
   Ora riprova una volta (un altro worker può averlo completato nel frattempo) e
   poi rifiuta nominando il nodo, invece di appendere il run.

**Test:** `tests/unit/test_warm_loop_node_is_still_expandable.py`, 4 casi.
3 sono rossi senza il fix, tutti verdi con.

**Verifiche.**

| prova | esito |
|---|---|
| `tests/unit` + `tests/contract` | 1210 passati, 3 skipped, 0 falliti |
| esperimento 2, store nuovo | exit 0, 9,83 s, sei valori **identici all'oracolo** |
| esperimento 2, stesso store, 2° run | exit 0, 2,86 s, identici |
| esperimento 2, stesso store, 3° run | exit 0, 2,61 s, identici |
| costo della mancata potatura (warm pre-fix contro post-fix) | 2,50 s contro 2,61-2,86 s |

Il riuso a caldo regge: la seconda e la terza esecuzione restano ~3,5x più
veloci della prima, e non potare più il nodo loop non si misura oltre il rumore
su questo programma.

**Quello che resta.** Il percorso di materializzazione dei goal
(`strategy.py::_side_effect`) non cattura `NeedsExpansion` e gira a motore
spento, dove nessuno potrebbe comunque espandere — ed è esattamente il punto in
cui l'esperimento 4 muore ancora il 2026-09-09. Vedi l'aggiornamento in testa
alla sezione.

## 8. Lavoro costoso perso per un errore a valle — MEDIA

**Cosa è successo.** Il 2026-09-07 un training di 1000 epoche è arrivato in
fondo — tredici ore, Dice di validazione 0,8856, `checkpoint_final.pth`
scritto, postprocessing determinato — e poi il run è morto:

```
ERROR: nnUNet training failed: Object of type int64 is not JSON serializable
```

Tutto ciò che costava era riuscito. È fallita l'ultima riga di contabilità.

**Causa immediata, risolta** (`b0b68a1`). `_decision_from_pickle` restituiva i
kwargs del postprocessing come `dict(kw)`, e lì dentro gli id delle label sono
`numpy.int64`; finivano in `materialize.py:48` `json.dumps`. Corretto con
`_plain`, che converte al confine dove il docstring già dichiarava l'invariante
— *"values a model can carry"* — più `tests/unit/test_nnunet_decision_is_json.py`.

**Quello che resta è architetturale.** `nnunet.train_internal` è un kernel solo:
training, postprocessing e scrittura dello stato falliscono insieme. Qui le
tredici ore si sono salvate **solo perché nnU-Net tiene i suoi checkpoint su
disco per conto proprio** — per fortuna, non per progetto. Se il valore costoso
fosse stato in RAM sarebbe sparito.

Domande aperte, per Vincenzo: se un kernel di lunga durata debba poter rendere
durevole un risultato prima di passi che possono fallire; e se il confine del
nodo sia nel punto giusto, cioè se training, postprocessing e stato debbano
essere tre nodi invece di uno.

**Terza cosa, della stessa famiglia:** il motore fa content-addressing di questi
valori, quindi un `numpy.int64` non è solo non-JSON — non è nemmeno una chiave
stabile fra versioni di numpy. Serve un audit degli altri confini dove un valore
di libreria esterna finisce su un handle o nello store.

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

**Deciso il 2026-09-07: per ora i sei si ignorano.** Nessuno dei tre programmi
BraTS spediti nell'artifact è fra questi, e la guardia nel motore fa sì che non
possano più fingere di aver funzionato. Restano da sistemare o ritirare quando
si rimette mano alla gallery; tre sono citati in `doc/gallery/README.md` e
`doc/user/language-gallery.md`, quindi ritirarli tocca anche quelle tabelle.

Correzione di un'affermazione precedente: avevo scritto che
`--no-execute --save-task-graph` produce un grafo *vuoto* per
`tropical_slices.imgql`. Il file non è vuoto — contiene una riga di riepilogo,
`WorkPlan(nodes=4, goals=0, imports=[...])`. Il mio `wc -l` diceva 0 solo perché
manca l'a-capo finale. La sostanza regge (4 nodi, zero goal, niente da
calcolare), la frase era imprecisa e sta anche nel commit `81f001e`.

---

## 2. Oracolo — FATTO per gli esperimenti 2 e 3, PROVVISORIO per il 4

**Fatto il 2026-09-08.** I valori attesi vivono in `ORACLE`, dentro
`tools/make_artifact.py`, e il README del revisore li rende in una tabella per
esperimento. Sono dati, non prosa, così si sostituiscono in blocco quando
avremo più run; e lo script si rifiuta di costruire se `ORACLE` contiene un
programma che l'artifact non spedisce.

**Esperimento 2** (cinque casi) — tolleranza **0**.

| goal | valore |
|---|---|
| `case_002_best` | 0,8959418354477335 |
| `case_079_best` | **0,0** |
| `case_089_best` | 0,6518925047022751 |
| `case_230_best` | 0,9584736373313636 |
| `case_328_best` | 0,9131447438872032 |
| `average_best` | 0,6838905442737151 |

Scoperta utile: **l'oracolo esisteva già** e non lo sapevamo.
`doc/gallery/programs/brats2020/README.md` ha una tabella "What it prints" con
gli stessi valori al terzo decimale, misurati indipendentemente e prima. Girano
uguali sullo stack pinnato. Lo zero sul caso 079 è la risposta giusta, non un
fallimento — il tumore più piccolo del dataset non alza nessun seme — ed è nel
campione apposta. Nel README dell'artifact quello zero ha la sua spiegazione
accanto, altrimenti un revisore lo legge come un errore.

**Esperimento 3** (AIIM) — tolleranza **0**, sei run su due giorni: cache calda,
store vuoto, `--no-cache` a 1, 4 e 16 thread. Bit-identici ogni volta.

| goal | valore |
|---|---|
| `dice_fixed_mean` | 0,8069413698956456 |
| `dice_best_mean` | 0,8513501430954795 |
| `dice_best_median` | 0,8904556073150862 |
| `dice_best_stdev` | 0,10944800755354497 |
| `vi_thr_median` | 0,91 |
| `vi_thr_distribution` | `[[0.81,2],[0.83,1],[0.85,1],[0.86,1],[0.87,1],[0.88,1],[0.89,1],[0.9,2],[0.92,10]]` |

**Esperimento 4** (nnU-Net) — **provvisorio**, tolleranza **10⁻⁴** sulle Dice,
**esatta** su soglie e distribuzione. Due soli run completi, e non coincidono.

Misurato fra i due:

| | |
|---|---|
| `best_dice` cambiati | 6 su 20 |
| scarto massimo | **2,7·10⁻⁵** |
| aggregati (`model_dice_mean`, `dice_best_*`) | si muovono dalla sesta cifra |
| `best_vi_thr`, `vi_thr_distribution` | **identiche** |

Questo è il numero che serviva. Il README prometteva al revisore che *"quello
che deve riprodursi è la forma del risultato, non le cifre"*: adesso non è più
una dichiarazione di principio, è misurata, con un ordine di grandezza. La
tolleranza 10⁻⁴ copre con margine il 2,7·10⁻⁵ osservato.

**Perché resta provvisorio.** Due run non sono una distribuzione. E soprattutto
l'esperimento 4 oggi gira una volta su tre — vedi il punto 7 e il bug di
rematerializzazione qui sotto. Un oracolo su un esperimento che fallisce due
volte su tre è un aneddoto con delle cifre. Va rifatto quando il motore è
affidabile.

**Cosa manca ancora.** Un `verify.sh` che confronti e ritorni diverso da zero.
Oggi il revisore ha i numeri e li guarda; non ha un comando che glielo dica.

---

## 3. Percorsi dataset assoluti — MITIGATO NELL'ARTIFACT, APERTO NEL REPO

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

**Mitigato il 2026-09-08, per l'artifact.** Il README generato elenca in una
tabella ogni percorso da cambiare, letto dai file **copiati** invece che scritto
a mano. Il revisore passa da "cercare in 11 file" a "cambiare 4 righe che ti
abbiamo elencato":

```
brats-five-cases.imgql:            dataset_root = ./doc/gallery/programs/brats2020/data
brats-threshold-sweep-aiim.imgql:  dataset_root = /home/VoxLogicA/datasets/MICCAI_BraTS2020_TrainingData
brats-threshold-sweep-nnunet.imgql: dataset_root = /home/VoxLogicA/datasets/...
brats-threshold-sweep-nnunet.imgql: work_root    = /home/laura/nnunet-brats-work
```

E se sbaglia, `dir` fallisce invece di restituire una lista vuota.

**Quello che resta aperto.** La stringa specifica di questa macchina è ancora
dentro il programma e va editata a mano; e nel repo il problema è intatto —
l'artifact non è il repo. Da **alta** scende a **bassa**, non a chiuso.

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

## 5. Manifest del dataset — FATTO il 2026-09-08

`tools/dataset_manifest.py` più `tools/brats2020-manifest.json` (404 KB, 1845
file, sha256 per ciascuno). Entrambi viaggiano nell'artifact, e il README dice
al revisore di lanciarlo prima di tutto il resto.

```
python3 tools/dataset_manifest.py /path/to/MICCAI_BraTS2020_TrainingData
```

**Non conta i file: confronta l'ordine.** I programmi selezionano per posizione,
quindi il contratto è la sequenza, non la cardinalità. Su un disallineamento lo
strumento nomina **l'indice** in cui le due liste divergono, perché da lì in poi
ogni posizione indica un caso diverso.

Verificato in entrambe le direzioni:

| prova | esito |
|---|---|
| dataset vero, 369 casi | `OK`, exit 0, 1845 file per dimensione e sha256, ~3 s |
| copia con il caso 005 rimosso | `FAILED`, exit 1, *"diverges at index 4"* |

L'indice 4 è esattamente il caso tolto. Un AIIM su quella copia sarebbe girato
senza errori restituendo Dice plausibili e non confrontabili.

**Una trappola trovata scrivendolo, che riguarda il motore e non lo strumento.**
`dir` con `full_paths` costruisce `str(entry.resolve())` e ordina **quello**
(`primitives/default/dir.py`). Se il dataset è una fattoria di symlink, l'ordine
segue i **target**, non i nomi sotto la root: il revisore vede un ordine e i
programmi ne ricevono un altro, senza nessun segnale. Lo strumento lo rileva e
lo dice; il README dell'artifact istruisce a collegare intere directory di caso
da un unico posto, o a copiare.

Nota di merito al dataset che abbiamo: i glob sono puliti, 369 file per
modalità, nessuna sottodirectory che inquini la ricerca ricorsiva.

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

## Determinismo dei valori — VERIFICATO, con una riserva

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

**La riserva, aggiunta il 2026-09-08.** Questo dice che i valori NON dipendono
dal numero di thread. Non dice che il run arrivi in fondo: il punto 7 è una race
che uccide il processo a parallelismo alto, e i due enunciati sono indipendenti.
"Deterministico" e "affidabile" sono cose diverse, e qui abbiamo il primo senza
il secondo.

**Cosa non copre, inoltre.** Una macchina sola (stessa CPU, stesso SimpleITK —
vedi punto 4); un programma solo; e nulla sul percorso GPU. Il programma nnU-Net
resta non riproducibile per costruzione (GPU, seed, cuDNN) a meno di congelare
e distribuire i pesi.

---

## Fuori perimetro, per decisione

I dataset BraTS non vanno su Zenodo né altrove: è policy BraTS, i revisori se
li procurano da soli. Tutto quanto sopra assume questo vincolo.
