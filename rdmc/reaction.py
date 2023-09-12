#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Module for Reaction
"""

from collections import Counter
from functools import reduce, wraps
from itertools import chain, product
from typing import List, Optional, Tuple, Union

from rdkit.Chem import rdChemReactions, rdFMCS
from rdkit.Chem.Draw import rdMolDraw2D

from rdmc import RDKitMol
from rdmc.mol import generate_radical_resonance_structures
from rdmc.ts import get_all_changing_bonds


class Reaction:

    """
    The Reaction class that stores the reactant, product, and transition state information.
    """

    def __init__(self,
                 reactant: Union[List[RDKitMol], RDKitMol],
                 product: Union[List[RDKitMol], RDKitMol],
                 ts: Optional['RDKitMol'] = None,
                 ):
        """
        Initialize the Reaction class.

        Args:
            smiles (str, optional): The reaction SMILES. Defaults to None.
            reactant (List[RDKitMol] or RDKitMol, optional): The reactant molecule(s) or the reactant complex.
                                                             Defaults to None.
            product (List[RDKitMol] or RDKitMol, optional): The product molecule(s) or the product complex.
                                                            Defaults to None.
        """
        self.init_reactant_product(reactant=reactant, product=product)
        if ts is not None:
            self.ts = ts

    def __str__(self):
        """
        Return the reaction SMILES.
        """
        return self.to_smiles()

    def _repr_svg_(self):
        """
        Return the reaction SVG.
        """
        return self.draw_2d()

    @classmethod
    def from_reactant_and_product_smiles(cls,
                                         rsmi: Union[List[str], str],
                                         psmi: Union[List[str], str]):
        """
        Initialize the Reaction class from reactant and product smile(s).

        Args:

        """
        if isinstance(rsmi, list):
            rsmi = '.'.join(rsmi)
        if isinstance(psmi, list):
            psmi = '.'.join(psmi)
        try:
            reactant = RDKitMol.FromSmiles(rsmi,
                                           removeHs=False,
                                           addHs=True,
                                           sanitize=True,
                                           keepAtomMap=True)
        except Exception as exc:
            raise ValueError(f'Got invalid reactant smiles ({rsmi})') from exc
        try:
            product = RDKitMol.FromSmiles(psmi,
                                          removeHs=False,
                                          addHs=True,
                                          sanitize=True,
                                          keepAtomMap=True)
        except Exception as exc:
            raise ValueError(f'Got invalid product smiles ({psmi})') from exc

        return cls(reactant=reactant, product=product)

    @classmethod
    def from_reaction_smiles(cls,
                             smiles: str):
        """
        Initialize the Reaction class from reaction SMILES.

        Args:
            smiles (str): The reaction SMILES.

        Returns:
            Reaction: The Reaction class.
        """
        try:
            rsmi, psmi = smiles.split('>>')
        except ValueError as exc:
            raise ValueError('Not a valid reaction smiles, missing ">>".') from exc
        return cls.from_reactant_and_product_smiles(rsmi=rsmi, psmi=psmi)

    def init_reactant_product(self,
                              reactant: Union[List[RDKitMol], RDKitMol],
                              product: Union[List[RDKitMol], RDKitMol]):
        """
        """
        if isinstance(reactant, list):
            self.reactant = reactant
            self.reactant_complex = self._combine_multiple_mols(reactant)
        else:
            self.reactant = list(reactant.GetMolFrags(asMols=True))
            self.reactant_complex = reactant
        if isinstance(product, list):
            self.product = product
            self.product_complex = self._combine_multiple_mols(product)
        else:
            self.product = list(product.GetMolFrags(asMols=True))
            self.product_complex = product

    @staticmethod
    def _combine_multiple_mols(mols: List[RDKitMol]) -> RDKitMol:
        """
        Combine multiple molecules into a complex.

        Args:
            mols (List[RDKitMol]): The list of molecules to combine.
        """
        return reduce(lambda x, y: x.CombineMol(y), mols)

    @property
    def is_num_atoms_balanced(self) -> bool:
        """
        Whether the number of atoms in the reactant(s) and product(s) are balanced.
        """
        return self.reactant_complex.GetNumAtoms() == self.product_complex.GetNumAtoms()

    @property
    def is_element_balanced(self) -> bool:
        """
        Whether the elements in the reactant(s) and product(s) are balanced.
        """
        if self.is_num_atoms_balanced:
            return Counter(self.reactant_complex.GetElementSymbols()) == \
                Counter(self.product_complex.GetElementSymbols())
        return False

    @property
    def is_charge_balanced(self) -> bool:
        """
        Whether the charge in the reactant(s) and product(s) are balanced.
        """
        return self.reactant_complex.GetFormalCharge() == \
            self.product_complex.GetFormalCharge()

    @property
    def is_mult_equal(self) -> bool:
        """
        Whether the spin multiplicity in the reactant(s) and product(s) are equal.
        """
        return self.reactant_complex.GetSpinMultiplicity() == \
            self.product_complex.GetSpinMultiplicity()

    @property
    def num_atoms(self) -> bool:
        """
        The number of atoms involved in the reactant(s) and product(s).
        """
        assert self.is_num_atoms_balanced, "The number of atoms in the reactant(s) and product(s) are not balanced."
        return self.reactant_complex.GetNumAtoms()

    @property
    def num_reactants(self) -> int:
        """
        The number of reactants.
        """
        return len(self.reactant)

    @property
    def num_products(self) -> int:
        """
        The number of products.
        """
        return len(self.product)

    def require_bond_analysis(func):
        """
        Timer decorator for recording the time of a function.

        Args:
            func (function): The function to be decorated.

        Returns:
            function: The decorated function.
        """
        wraps(func)

        def wrapper(self, *args, **kwargs):
            try:
                return func(self, *args, **kwargs)
            except AttributeError:
                self._formed_bonds, self._broken_bonds, self._changed_bonds = get_all_changing_bonds(
                    r_mol=self.reactant_complex,
                    p_mol=self.product_complex,
                )
                return func(self, *args, **kwargs)
        return wrapper

    def bond_analysis(self):
        """
        Perform bond analysis on the reaction.
        """
        self._formed_bonds, self._broken_bonds, self._changed_bonds = get_all_changing_bonds(
            r_mol=self.reactant_complex,
            p_mol=self.product_complex,
        )

    @property
    @require_bond_analysis
    def num_broken_bonds(self) -> int:
        """
        The number of bonds broken in the reaction.
        """
        return len(self._broken_bonds)

    @property
    @require_bond_analysis
    def num_formed_bonds(self) -> int:
        """
        The number of bonds broken in the reaction.
        """
        return len(self._formed_bonds)

    @property
    @require_bond_analysis
    def num_changed_bonds(self) -> int:
        """
        The number of bonds with bond order changed in the reaction.
        """
        return len(self._changed_bonds)

    @property
    @require_bond_analysis
    def broken_bonds(self) -> List[Tuple[int]]:
        """
        The bonds broken in the reaction.
        """
        return self._broken_bonds

    @property
    @require_bond_analysis
    def formed_bonds(self) -> List[Tuple[int]]:
        """
        The bonds formed in the reaction.
        """
        return self._formed_bonds

    @property
    @require_bond_analysis
    def changed_bonds(self) -> List[Tuple[int]]:
        """
        The bonds with bond order changed in the reaction.
        """
        return self._changed_bonds

    @property
    @require_bond_analysis
    def active_bonds(self) -> List[Tuple[int]]:
        """
        The bonds broken and formed in the reaction.
        """
        return self._broken_bonds + self._formed_bonds

    @property
    @require_bond_analysis
    def involved_bonds(self) -> List[Tuple[int]]:
        """
        The bonds broken and formed in the reaction.
        """
        return self._broken_bonds + self._formed_bonds + self._changed_bonds

    @property
    @require_bond_analysis
    def active_atoms(self) -> List[int]:
        """
        The atoms involved in the bonds broken and formed in the reaction.
        """
        return list(set(chain(*self.active_bonds)))

    @property
    @require_bond_analysis
    def involved_atoms(self) -> List[int]:
        """
        The atoms involved in the bonds broken and formed in the reaction.
        """
        return list(set(chain(*self.involved_bonds)))

    def apply_resonance_correction(self,
                                   inplace: bool = True,
                                   kekulize: bool = True,
                                   ) -> 'Reaction':
        """
        Apply resonance correction to the reactant and product complexes.
        """
        try:
            rcps = generate_radical_resonance_structures(self.reactant_complex, kekulize=kekulize)
        except BaseException:
            rcps = [self.reactant_complex]
        try:
            pcps = generate_radical_resonance_structures(self.product_complex, kekulize=kekulize)
        except BaseException:
            pcps = [self.product_complex]

        n_changed_bonds = self.num_changed_bonds
        rmol = self.reactant_complex
        pmol = self.product_complex

        modify_flag = False
        for rcp, pcp in product(rcps, pcps):
            _, _, new_changed_bonds = get_all_changing_bonds(rcp, pcp)
            if len(new_changed_bonds) < n_changed_bonds:
                modify_flag = True
                n_changed_bonds = len(new_changed_bonds)
                rmol, pmol = rcp, pcp

        if modify_flag:
            if inplace:
                self.init_reactant_product(rmol, pmol)
                self.bond_analysis()
                return self
            else:
                # todo: check if ts has 3d coordinates
                return Reaction(rmol, pmol, ts=self.ts)
        return self

    def get_reverse_reaction(self):
        """
        Get the reverse reaction.
        """
        return Reaction(self.product_complex,
                        self.reactant_complex,
                        ts=self.ts)

    def to_smiles(self,
                  remove_hs: bool = False,
                  remove_atom_map: bool = False,
                  **kwargs,
                  ) -> str:
        """
        Convert the reaction to reaction SMILES.
        """
        rsmi = self.reactant_complex.ToSmiles(removeAtomMap=remove_atom_map,
                                              removeHs=remove_hs,
                                              **kwargs)
        psmi = self.product_complex.ToSmiles(removeAtomMap=remove_atom_map,
                                             removeHs=remove_hs,
                                             **kwargs)
        return f'{rsmi}>>{psmi}'

    def make_ts(self):
        """
        Make the transition state of the reaction based on the reactant and product.
        This method assumes that the reactant complex and product complex are atom-mapped
        already.
        """
        self.ts = self.reactant_complex.AddRedundantBonds(self.formed_bonds)
        return self.ts

    def _update_ts(self):
        """
        Update the transition state of the reaction. Assign reaction, reactant,
        and product attributes to the transition state based on the reaction.
        """
        if not hasattr(self._ts, 'reaction'):
            self._ts.reaction = self
        if not hasattr(self._ts, 'reactant'):
            self._ts.reactant = self.reactant_complex
        if not hasattr(self._ts, 'product'):
            self._ts.product = self.product_complex

    @property
    def ts(self):
        """
        The transition state of the reaction.
        """
        if not hasattr(self, '_ts'):
            self.make_ts()
        self._update_ts()
        return self._ts

    @ts.setter
    def ts(self,
           mol: 'RDKitMol'):
        """
        Set the transition state of the reaction.
        """
        self._ts = mol
        self._update_ts()

    def to_rdkit_reaction(self) -> rdChemReactions.ChemicalReaction:
        """
        Convert the reaction to RDKit ChemicalReaction.
        """
        return rdChemReactions.ReactionFromSmarts(self.to_smiles(),
                                                  useSmiles=True)

    def draw_2d(self,
                font_scale: float = 1.0,
                highlight_by_reactant: bool = True,
                ) -> str:
        """
        This is a modified version of the drawReaction2D function in RDKit.

        Args:
            font_scale (float, optional): The font scale for the atom map number. Defaults to 1.0.
            highlightByReactant (bool, optional): Whether to highlight the reactant(s) or product(s). Defaults to True.

        Returns:
            str: The SVG string. To display the SVG, use IPython.display.SVG(svg_string).
        """
        def move_atommaps_to_notes(mol):
            for atom in mol.GetAtoms():
                if atom.GetAtomMapNum():
                    atom.SetProp("atomNote", str(atom.GetAtomMapNum()))

        rxn = self.to_rdkit_reaction()

        # move atom maps to be annotations:
        for mol in rxn.GetReactants():
            move_atommaps_to_notes(mol)
        for mol in rxn.GetProducts():
            move_atommaps_to_notes(mol)

        d2d = rdMolDraw2D.MolDraw2DSVG(800, 300)
        d2d.drawOptions().annotationFontScale = font_scale
        d2d.DrawReaction(rxn,
                         highlightByReactant=highlight_by_reactant)

        d2d.FinishDrawing()

        return d2d.GetDrawingText()
