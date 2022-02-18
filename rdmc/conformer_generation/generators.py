#!/usr/bin/env python3
#-*- coding: utf-8 -*-

"""
Modules for conformer generation workflows
"""

from rdmc.mol import RDKitMol
from .pruners import TorsionPruner
import numpy as np
import logging


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(name)s | %(levelname)s: %(message)s",
    datefmt="%Y/%m/%d %I:%M:%S %p",
)


class StochasticConformerGenerator:
    """
    A module for stochastic conformer generation. The workflow follows an embed -> optimize -> prune cycle with
    custom stopping criteria. Additional final modules can be added at the user's discretion.
    """
    def __init__(self, smiles, embedder, optimizer, pruner,
                 metric, min_iters=5, max_iters=100, final_modules=None):
        """
        Generate an RDKitMol Molecule instance from a RDKit ``Chem.rdchem.Mol`` or ``RWMol`` molecule.

        Args:
            smiles (str): SMILES input for which to generate conformers.
            embedder (class): Instance of an embedder from embedders.py.
            optimizer (class): Instance of a optimizer from optimizers.py.
            pruner (class): Instance of a pruner from pruners.py.
            metric (class): Instance of a metric from metrics.py.
            min_iters (int): Minimum number of iterations for which to run the module (default=5).
            max_iters (int}: Maximum number of iterations for which to run the module (default=100).
            final_modules (List): List of instances of optimizer/pruner to run after initial cycles complete.
        """

        self.logger = logging.getLogger(f"{self.__class__.__name__}")

        self.smiles = smiles
        self.embedder = embedder
        self.optimizer = optimizer
        self.pruner = pruner
        self.metric = metric

        if not optimizer:
            self.metric.metric = "total conformers"
            self.logger.info("No optimizer selected: termination criteria set to total conformers")

        self.mol = RDKitMol.FromSmiles(smiles)
        self.unique_mol_data = []
        self.iter = 0
        self.min_iters = min_iters
        self.max_iters = max_iters
        self.final_modules = [] if not final_modules else final_modules

        if isinstance(self.pruner, TorsionPruner):
            self.pruner.initialize_torsions_list(smiles)

    def __call__(self, n_conformers_per_iter):

        self.logger.info(f"Generating conformers for {self.smiles}")
        for it in range(self.max_iters):
            self.iter += 1

            self.logger.info(f"\nIteration {self.iter}: embedding {n_conformers_per_iter} initial guesses...")
            initial_mol_data = self.embedder(self.smiles, n_conformers_per_iter)

            if self.optimizer:
                self.logger.info(f"Iteration {self.iter}: optimizing initial guesses...")
                opt_mol_data = self.optimizer(initial_mol_data)
            else:
                # TODO: fix default behavior when no optimizer specified
                opt_mol_data = []
                for c_id in range(len(initial_mol_data)):
                    conf = initial_mol_data[c_id]["conf"]
                    positions = conf.GetPositions()
                    opt_mol_data.append({"positions": positions,
                                         "conf": conf,
                                         "energy": np.nan})

            # check for failures
            if len(opt_mol_data) == 0:
                self.logger.info("Failed to optimize any of the embedded conformers")
                continue

            self.logger.info(f"Iteration {self.iter}: pruning conformers...")
            unique_mol_data = self.pruner(opt_mol_data, self.unique_mol_data)
            self.metric.calculate_metric(unique_mol_data)
            self.unique_mol_data = unique_mol_data
            self.logger.info(f"Iteration {self.iter}: kept {len(unique_mol_data)} unique conformers")

            if it < self.min_iters:
                continue

            if self.metric.check_metric():
                self.logger.info(f"Iteration {self.iter}: stop crietria reached\n")
                for module in self.final_modules:
                    self.logger.info(f"Calling {module.__class__.__name__}")
                    unique_mol_data = module(unique_mol_data)
                return unique_mol_data

        self.logger.info(f"Iteration {self.iter}: max iterations reached\n")
        for module in self.final_modules:
            self.logger.info(f"Calling {module.__class__.__name__}")
            unique_mol_data = module(unique_mol_data)
        return unique_mol_data