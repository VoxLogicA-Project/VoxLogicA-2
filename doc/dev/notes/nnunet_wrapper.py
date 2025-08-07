"""
nnU-Net v2 Dask Integration Wrapper
==================================

Wrapper Python per nnU-Net v2 con supporto Dask, conforme alla documentazione ufficiale.

**Requisiti ambiente:**
- Python >= 3.9
- PyTorch installato manualmente (https://pytorch.org/get-started/locally/)
- nnunetv2 (`pip install nnunetv2`)
- dask[complete], numpy, nibabel

**requirements.txt consigliato:**
    dask[complete]>=2023.5.0
    numpy>=1.21.0
    nibabel>=3.2.0
    pathlib
    nnunetv2

**ATTENZIONE:** torch/torchvision/torchaudio NON vanno messi in requirements.txt, vanno installati manualmente per la propria GPU/CPU/MPS!

**Dataset format:**
- Struttura: nnUNet_raw/DatasetXXX_Name/{imagesTr,labelsTr,imagesTs,dataset.json}
- Nomi immagini: {CASE_NAME}_{MODALITY_IDX:04d}.nii.gz (o altro formato supportato)
- Nomi label: {CASE_NAME}.nii.gz
- dataset.json conforme a https://github.com/MIC-DKFZ/nnUNet/blob/master/documentation/dataset_format.md

**Formati file:**
- Supportati: .nii.gz, .nrrd, .mha, .tif, .png, ... (vedi doc nnU-Net v2)
- Il wrapper di default usa .nii.gz ma può essere esteso facilmente

**Multi-modalità:**
- Ogni caso può avere più modalità (es: T1, T2, FLAIR), ogni modalità è un file separato
- Tutte le modalità e label di un caso devono avere la stessa geometria

**Multi-GPU:**
- nnU-Net v2 NON supporta multi-GPU in un singolo processo: lanciare più processi, uno per GPU, ognuno su un fold diverso

**Controlli automatici:**
- Il wrapper verifica la presenza di torch e nnunetv2 e la versione di Python
- Warning se mancano dipendenze critiche

**Workflow:**
1. Conversione Dask bag → struttura file nnU-Net
2. Preprocessing e planning (nnUNetv2_plan_and_preprocess)
3. Training (nnUNetv2_train, un fold per GPU/processo)
4. Prediction (nnUNetv2_predict)

**Per dettagli e troubleshooting:**
- https://github.com/MIC-DKFZ/nnUNet/blob/master/documentation/installation_instructions.md
- https://github.com/MIC-DKFZ/nnUNet/blob/master/documentation/dataset_format.md
- https://github.com/MIC-DKFZ/nnUNet/blob/master/documentation/how_to_use_nnunet.md
"""

import os
import json
import shutil
import tempfile
import subprocess
import logging
from pathlib import Path
from typing import Dict, List, Optional, Union, Any, Callable
import dask.bag as db
import numpy as np
import nibabel as nib
from datetime import datetime


class nnUNetDaskWrapper:
    """
    Wrapper per nnU-Net v2 che gestisce training e prediction da Dask bags.
    """
    def __init__(self, work_dir: str, dataset_id: int = 1, dataset_name: str = "CustomDataset", file_ending: str = ".nii.gz", overwrite_image_reader_writer: str = None):
        """
        Inizializza il wrapper.
        
        Args:
            work_dir: Directory di lavoro dove salvare tutto
            dataset_id: ID numerico del dataset (default: 1)
            dataset_name: Nome del dataset (default: "CustomDataset")
            file_ending: Estensione file immagini/label (default: .nii.gz)
            overwrite_image_reader_writer: Forza un reader/writer specifico (es. "NibabelIO", "SimpleITKIO")
        """
        self.work_dir = Path(work_dir)
        self.dataset_id = dataset_id
        self.dataset_name = dataset_name
        self.dataset_full_name = f"Dataset{dataset_id:03d}_{dataset_name}"
        self.file_ending = file_ending
        self.overwrite_image_reader_writer = overwrite_image_reader_writer
        # Setup directories
        self.setup_directories()
        # Setup logging
        self.setup_logging()
        # Controlli ambiente
        self._check_environment()

    def _check_environment(self):
        import sys
        # Python version
        if sys.version_info < (3, 9):
            raise RuntimeError("nnUNetDaskWrapper richiede Python >= 3.9")
        # torch
        try:
            import torch
        except ImportError:
            self.logger.warning("ATTENZIONE: torch non trovato! Installa PyTorch manualmente per la tua GPU/CPU/MPS.")
        # nnunetv2
        try:
            import nnunetv2
        except ImportError:
            self.logger.warning("ATTENZIONE: nnunetv2 non trovato! Installa con 'pip install nnunetv2'.")

    def setup_directories(self):
        """Imposta le directory necessarie per nnU-Net."""
        self.work_dir.mkdir(parents=True, exist_ok=True)
        
        # Directory principali nnU-Net
        self.nnunet_raw = self.work_dir / "nnUNet_raw"
        self.nnunet_preprocessed = self.work_dir / "nnUNet_preprocessed"
        self.nnunet_results = self.work_dir / "nnUNet_results"
        
        # Directory del dataset
        self.dataset_dir = self.nnunet_raw / self.dataset_full_name
        self.images_tr = self.dataset_dir / "imagesTr"
        self.labels_tr = self.dataset_dir / "labelsTr"
        self.images_ts = self.dataset_dir / "imagesTs"
        
        # Crea tutte le directory
        for dir_path in [self.nnunet_raw, self.nnunet_preprocessed, self.nnunet_results,
                        self.images_tr, self.labels_tr, self.images_ts]:
            dir_path.mkdir(parents=True, exist_ok=True)
            
        # Imposta variabili d'ambiente nnU-Net
        os.environ["nnUNet_raw"] = str(self.nnunet_raw)
        os.environ["nnUNet_preprocessed"] = str(self.nnunet_preprocessed)
        os.environ["nnUNet_results"] = str(self.nnunet_results)
        
    def setup_logging(self):
        """Imposta il logging."""
        log_file = self.work_dir / "nnunet_wrapper.log"
        logging.basicConfig(
            level=logging.INFO,
            format='%(asctime)s - %(levelname)s - %(message)s',
            handlers=[
                logging.FileHandler(log_file),
                logging.StreamHandler()
            ]
        )
        self.logger = logging.getLogger(__name__)
        
    def save_dask_data(self, 
                      images_bag: db.Bag, 
                      labels_bag: db.Bag,
                      modalities: List[str],
                      test_images_bag: Optional[db.Bag] = None) -> Dict[str, Any]:
        """
        Salva i dati dai Dask bags nel formato nnU-Net.
        
        Args:
            images_bag: Bag contenente le immagini (formato: (case_id, modalità, array_numpy))
            labels_bag: Bag contenente le label (formato: (case_id, array_numpy))  
            modalities: Lista delle modalità (es: ["T1", "T2", "FLAIR"])
            test_images_bag: Bag opzionale con immagini di test
            
        Returns:
            Dizionario con informazioni del dataset
        """
        self.logger.info("Inizio salvataggio dati da Dask bags...")
        
        # Raccogli i dati
        images_data = images_bag.compute()
        labels_data = labels_bag.compute()
        
        # Organizza i dati per case_id
        images_by_case = {}
        for case_id, modality, img_array in images_data:
            if case_id not in images_by_case:
                images_by_case[case_id] = {}
            images_by_case[case_id][modality] = img_array
            
        labels_by_case = {case_id: label_array for case_id, label_array in labels_data}
        
        # Verifica consistenza
        training_cases = set(images_by_case.keys()) & set(labels_by_case.keys())
        self.logger.info(f"Trovati {len(training_cases)} casi di training")
        
        # Salva immagini di training e labels
        training_list = []
        for case_id in sorted(training_cases):
            case_name = f"{self.dataset_name}_{case_id:03d}"
            
            # Salva ogni modalità
            for mod_idx, modality in enumerate(modalities):
                if modality in images_by_case[case_id]:
                    img_array = images_by_case[case_id][modality]
                    img_filename = f"{case_name}_{mod_idx:04d}.nii.gz"
                    self._save_nifti(img_array, self.images_tr / img_filename)
                else:
                    self.logger.warning(f"Modalità {modality} mancante per caso {case_id}")
                    
            # Salva label
            if case_id in labels_by_case:
                label_array = labels_by_case[case_id]
                label_filename = f"{case_name}.nii.gz"
                self._save_nifti(label_array, self.labels_tr / label_filename)
                
            training_list.append({"image": case_name, "label": case_name})
            
        # Salva immagini di test se fornite
        test_list = []
        if test_images_bag is not None:
            test_images_data = test_images_bag.compute()
            test_images_by_case = {}
            for case_id, modality, img_array in test_images_data:
                if case_id not in test_images_by_case:
                    test_images_by_case[case_id] = {}
                test_images_by_case[case_id][modality] = img_array
                
            for case_id in sorted(test_images_by_case.keys()):
                case_name = f"{self.dataset_name}_{case_id:03d}"
                
                for mod_idx, modality in enumerate(modalities):
                    if modality in test_images_by_case[case_id]:
                        img_array = test_images_by_case[case_id][modality]
                        img_filename = f"{case_name}_{mod_idx:04d}.nii.gz"
                        self._save_nifti(img_array, self.images_ts / img_filename)
                        
                test_list.append(case_name)
        
        # Genera dataset.json
        dataset_info = self._create_dataset_json(training_list, test_list, modalities)
        
        self.logger.info(f"Dati salvati con successo: {len(training_list)} training, {len(test_list)} test")
        return dataset_info
        
    def _save_nifti(self, array: np.ndarray, filepath: Path):
        """Salva un array numpy come file (default: NIfTI, ma estendibile)."""
        # Per ora solo .nii.gz, .nrrd, .mha (via nibabel)
        if str(filepath).endswith(('.nii.gz', '.nii', '.nrrd', '.mha')):
            affine = np.eye(4)
            nii_img = nib.Nifti1Image(array, affine)
            nib.save(nii_img, str(filepath))
        else:
            self.logger.warning(f"Formato file non supportato dal wrapper: {filepath.suffix}. Estendi _save_nifti per supporto.")
            raise NotImplementedError(f"Formato file non supportato: {filepath.suffix}")

    def _create_dataset_json(self, training_list: List[Dict], test_list: List[str], modalities: List[str]) -> Dict[str, Any]:
        """Crea il file dataset.json richiesto da nnU-Net v2 (vedi doc ufficiale)."""
        labels = self._extract_labels()
        dataset_json = {
            "channel_names": {str(i): mod for i, mod in enumerate(modalities)},
            "labels": labels,
            "numTraining": len(training_list),
            "numTest": len(test_list),
            "file_ending": self.file_ending,
            "dataset_name": self.dataset_name,
            "reference": "Generated by nnUNetDaskWrapper",
            "licence": "Custom",
            "release": "1.0",
            "tensorImageSize": "4D",
            "training": training_list,
            "test": test_list
        }
        if self.overwrite_image_reader_writer:
            dataset_json["overwrite_image_reader_writer"] = self.overwrite_image_reader_writer
        json_path = self.dataset_dir / "dataset.json"
        with open(json_path, 'w') as f:
            json.dump(dataset_json, f, indent=2)
        return dataset_json
        
    def _extract_labels(self) -> Dict[str, int]:
        """Estrae automaticamente le labels uniche dai file salvati."""
        labels = {"background": 0}
        label_idx = 1
        
        # Esamina alcuni file di label per trovare valori unici
        for label_file in list(self.labels_tr.glob("*.nii.gz"))[:5]:  # Esamina solo i primi 5
            try:
                img = nib.load(str(label_file))
                unique_values = np.unique(img.get_fdata())
                for val in unique_values:
                    val = int(val)
                    if val > 0 and f"label_{val}" not in labels:
                        labels[f"label_{val}"] = label_idx
                        label_idx += 1
            except Exception as e:
                self.logger.warning(f"Errore leggendo {label_file}: {e}")
                
        return labels
        
    def train(self,
              images_bag: db.Bag,
              labels_bag: db.Bag, 
              modalities: List[str],
              configuration: str = "3d_fullres",
              nfolds: int = 5,
              test_images_bag: Optional[db.Bag] = None,
              trainer_class: str = "nnUNetTrainer",
              plans_name: str = "nnUNetPlans",
              resume: bool = True,
              num_gpus: int = 1,
              continue_training: bool = False,
              only_run_fold: Optional[int] = None,
              disable_checkpointing: bool = False) -> Dict[str, Any]:
        """
        Esegue il training completo di nnU-Net.
        
        Args:
            images_bag: Dask bag con immagini (case_id, modalità, array)
            labels_bag: Dask bag con labels (case_id, array)
            modalities: Lista modalità (es: ["T1", "T2", "FLAIR"])
            configuration: Configurazione nnU-Net ("2d", "3d_fullres", "3d_lowres")
            nfolds: Numero di fold per cross-validation
            test_images_bag: Bag opzionale con immagini test
            trainer_class: Classe trainer da usare
            plans_name: Nome del piano da usare
            resume: Se riprendere training interrotto
            num_gpus: Numero di GPU da usare
            continue_training: Continua training da checkpoint
            only_run_fold: Esegui solo un fold specifico
            disable_checkpointing: Disabilita il checkpointing
            
        Returns:
            Dizionario con risultati del training
        """
        self.logger.info("=== INIZIO TRAINING nnU-Net ===")
        
        # Salva i dati
        dataset_info = self.save_dask_data(images_bag, labels_bag, modalities, test_images_bag)
        
        try:
            # 1. Preprocessing e planning
            self._run_preprocessing(nfolds, plans_name)
            
            # 2. Training
            results = self._run_training(
                configuration=configuration,
                nfolds=nfolds,
                trainer_class=trainer_class,
                plans_name=plans_name,
                resume=resume,
                num_gpus=num_gpus,
                continue_training=continue_training,
                only_run_fold=only_run_fold,
                disable_checkpointing=disable_checkpointing
            )
            
            self.logger.info("=== TRAINING COMPLETATO ===")
            return results
            
        except Exception as e:
            self.logger.error(f"Errore durante il training: {e}")
            raise
            
    def _run_preprocessing(self, nfolds: int, plans_name: str):
        """Esegue preprocessing e planning."""
        self.logger.info("Inizio preprocessing...")
        
        cmd = [
            "nnUNetv2_plan_and_preprocess",
            "-d", str(self.dataset_id),
            "--verify_dataset_integrity",
            "-c", str(nfolds),
            "-pl", plans_name
        ]
        
        result = subprocess.run(cmd, capture_output=True, text=True, cwd=str(self.work_dir))
        
        if result.returncode != 0:
            self.logger.error(f"Preprocessing fallito: {result.stderr}")
            raise RuntimeError(f"Preprocessing fallito: {result.stderr}")
            
        self.logger.info("Preprocessing completato")
        
    def _run_training(self, **kwargs) -> Dict[str, Any]:
        """Esegue il training per tutti i fold."""
        results = {"fold_results": {}, "training_time": None}
        start_time = datetime.now()
        
        nfolds = kwargs.get('nfolds', 5)
        only_run_fold = kwargs.get('only_run_fold')
        
        folds_to_run = [only_run_fold] if only_run_fold is not None else range(nfolds)
        
        for fold in folds_to_run:
            self.logger.info(f"Training fold {fold}...")
            
            cmd = self._build_training_command(fold, **kwargs)
            
            result = subprocess.run(cmd, capture_output=True, text=True, cwd=str(self.work_dir))
            
            if result.returncode == 0:
                self.logger.info(f"Fold {fold} completato con successo")
                results["fold_results"][fold] = {"status": "success", "output": result.stdout}
            else:
                self.logger.error(f"Fold {fold} fallito: {result.stderr}")
                results["fold_results"][fold] = {"status": "failed", "error": result.stderr}
                
                # Se resume è False, interrompi al primo errore
                if not kwargs.get('resume', True):
                    raise RuntimeError(f"Training fold {fold} fallito: {result.stderr}")
                    
        results["training_time"] = str(datetime.now() - start_time)
        return results
        
    def _build_training_command(self, fold: int, **kwargs) -> List[str]:
        """Costruisce il comando di training."""
        cmd = [
            "nnUNetv2_train",
            str(self.dataset_id),
            kwargs.get('configuration', '3d_fullres'),
            str(fold),
            "-tr", kwargs.get('trainer_class', 'nnUNetTrainer'),
            "-p", kwargs.get('plans_name', 'nnUNetPlans')
        ]
        
        # Opzioni addizionali
        if kwargs.get('continue_training'):
            cmd.append("-c")
            
        if kwargs.get('disable_checkpointing'):
            cmd.append("--disable_checkpointing")
            
        if kwargs.get('num_gpus', 1) > 1:
            cmd.extend(["--npz", str(kwargs['num_gpus'])])
            
        return cmd
        
    def predict(self,
                input_images: Union[db.Bag, str, Path],
                output_dir: str,
                configuration: str = "3d_fullres", 
                trainer_class: str = "nnUNetTrainer",
                plans_name: str = "nnUNetPlans",
                folds: Optional[List[int]] = None,
                save_probabilities: bool = False,
                num_threads_preprocessing: int = 8,
                num_threads_nifti_save: int = 2) -> str:
        """
        Esegue prediction usando il modello allenato.
        
        Args:
            input_images: Dask bag, directory o lista di file di input
            output_dir: Directory dove salvare le predizioni
            configuration: Configurazione del modello
            trainer_class: Classe trainer utilizzata
            plans_name: Nome del piano utilizzato
            folds: Lista di fold da usare (None = tutti)
            save_probabilities: Salva anche le probabilità
            num_threads_preprocessing: Thread per preprocessing
            num_threads_nifti_save: Thread per salvataggio NIfTI
            
        Returns:
            Path della directory con le predizioni
        """
        self.logger.info("=== INIZIO PREDICTION ===")
        
        # Prepara directory di input
        if isinstance(input_images, db.Bag):
            input_dir = self._prepare_prediction_input_from_bag(input_images)
        else:
            input_dir = str(input_images)
            
        output_path = Path(output_dir)
        output_path.mkdir(parents=True, exist_ok=True)
        
        # Costruisci comando
        cmd = [
            "nnUNetv2_predict",
            "-i", input_dir,
            "-o", str(output_path),
            "-d", str(self.dataset_id),
            "-c", configuration,
            "-tr", trainer_class,
            "-p", plans_name,
            "--num_threads_preprocessing", str(num_threads_preprocessing),
            "--num_threads_nifti_save", str(num_threads_nifti_save)
        ]
        
        if folds:
            cmd.extend(["-f"] + [str(f) for f in folds])
            
        if save_probabilities:
            cmd.append("--save_probabilities")
            
        # Esegui predizione
        self.logger.info("Esecuzione predizione...")
        result = subprocess.run(cmd, capture_output=True, text=True, cwd=str(self.work_dir))
        
        if result.returncode != 0:
            self.logger.error(f"Predizione fallita: {result.stderr}")
            raise RuntimeError(f"Predizione fallita: {result.stderr}")
            
        self.logger.info(f"Predizione completata: {output_path}")
        return str(output_path)
        
    def _prepare_prediction_input_from_bag(self, images_bag: db.Bag) -> str:
        """Prepara input per predizione da Dask bag."""
        pred_input_dir = self.work_dir / "prediction_input"
        pred_input_dir.mkdir(exist_ok=True)
        
        # Pulisci directory precedente
        for f in pred_input_dir.glob("*"):
            if f.is_file():
                f.unlink()
                
        # Salva immagini dal bag
        images_data = images_bag.compute()
        images_by_case = {}
        
        for case_id, modality, img_array in images_data:
            if case_id not in images_by_case:
                images_by_case[case_id] = {}
            images_by_case[case_id][modality] = img_array
            
        # Determina modalità dai dati esistenti del training
        dataset_json_path = self.dataset_dir / "dataset.json"
        if dataset_json_path.exists():
            with open(dataset_json_path) as f:
                dataset_json = json.load(f)
            modalities = [dataset_json["channel_names"][str(i)] 
                         for i in range(len(dataset_json["channel_names"]))]
        else:
            # Fallback: usa modalità trovate nei dati
            all_modalities = set()
            for case_data in images_by_case.values():
                all_modalities.update(case_data.keys())
            modalities = sorted(list(all_modalities))
            
        # Salva file per ogni caso
        for case_id in sorted(images_by_case.keys()):
            case_name = f"{self.dataset_name}_{case_id:03d}"
            
            for mod_idx, modality in enumerate(modalities):
                if modality in images_by_case[case_id]:
                    img_array = images_by_case[case_id][modality]
                    img_filename = f"{case_name}_{mod_idx:04d}.nii.gz"
                    self._save_nifti(img_array, pred_input_dir / img_filename)
                    
        return str(pred_input_dir)
        
    def get_training_status(self) -> Dict[str, Any]:
        """Restituisce lo stato del training."""
        status = {
            "dataset_prepared": (self.dataset_dir / "dataset.json").exists(),
            "preprocessed": (self.nnunet_preprocessed / self.dataset_full_name).exists(),
            "trained_folds": []
        }
        
        # Controlla fold allenati
        results_dir = self.nnunet_results / self.dataset_full_name
        if results_dir.exists():
            for fold_dir in results_dir.glob("fold_*"):
                if (fold_dir / "checkpoint_final.pth").exists():
                    fold_num = int(fold_dir.name.split("_")[-1])
                    status["trained_folds"].append(fold_num)
                    
        return status
        
    def cleanup_temp_files(self):
        """Pulisce file temporanei."""
        temp_dirs = ["prediction_input"]
        for temp_dir in temp_dirs:
            temp_path = self.work_dir / temp_dir
            if temp_path.exists():
                shutil.rmtree(temp_path)
                self.logger.info(f"Rimossa directory temporanea: {temp_path}")


# Esempio di utilizzo
if __name__ == "__main__":
    # Esempio di come usare il wrapper
    import dask.bag as db
    
    # Crea wrapper
    wrapper = nnUNetDaskWrapper("/path/to/work_dir", dataset_id=1, dataset_name="MyDataset")
    
    # Simula dati (sostituire con i vostri Dask bags reali)
    # images_bag dovrebbe contenere tuple (case_id, modality, numpy_array)
    # labels_bag dovrebbe contenere tuple (case_id, numpy_array)
    
    # Training
    modalities = ["T1", "T2", "FLAIR"]
    results = wrapper.train(
        images_bag=your_images_bag,
        labels_bag=your_labels_bag,
        modalities=modalities,
        nfolds=5,
        resume=True
    )
    
    # Prediction
    predictions = wrapper.predict(
        input_images=your_test_images_bag,
        output_dir="/path/to/predictions"
    )