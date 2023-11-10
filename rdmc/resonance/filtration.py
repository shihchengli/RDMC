#!/usr/bin/env python3

###############################################################################
#                                                                             #
# RMG - Reaction Mechanism Generator                                          #
#                                                                             #
# Copyright (c) 2002-2023 Prof. William H. Green (whgreen@mit.edu),           #
# Prof. Richard H. West (r.west@neu.edu) and the RMG Team (rmg_dev@mit.edu)   #
#                                                                             #
# Permission is hereby granted, free of charge, to any person obtaining a     #
# copy of this software and associated documentation files (the 'Software'),  #
# to deal in the Software without restriction, including without limitation   #
# the rights to use, copy, modify, merge, publish, distribute, sublicense,    #
# and/or sell copies of the Software, and to permit persons to whom the       #
# Software is furnished to do so, subject to the following conditions:        #
#                                                                             #
# The above copyright notice and this permission notice shall be included in  #
# all copies or substantial portions of the Software.                         #
#                                                                             #
# THE SOFTWARE IS PROVIDED 'AS IS', WITHOUT WARRANTY OF ANY KIND, EXPRESS OR  #
# IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,    #
# FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE #
# AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER      #
# LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING     #
# FROM, OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER         #
# DEALINGS IN THE SOFTWARE.                                                   #
#                                                                             #
###############################################################################

"""
This module contains functions for filtering a list of Molecules representing a single Species,
keeping only the representative structures. Relevant for filtration of negligible mesomerism contributing structures.

The rules this module follows are (by order of importance):

    1. Minimum overall deviation from the Octet Rule (elaborated for Dectet for sulfur as a third row element)
    2. Additional charge separation is only allowed for radicals if it makes a new radical site in the species
    3. If a structure must have charge separation, negative charges will be assigned to more electronegative atoms,
       whereas positive charges will be assigned to less electronegative atoms (charge stabilization)
    4. Opposite charges will be as close as possible to one another, and vice versa (charge stabilization)

(inspired by http://web.archive.org/web/20140310074727/http://www.chem.ucla.edu/~harding/tutorials/resonance/imp_res_str.html
which is quite like http://www.chem.ucla.edu/~harding/IGOC/R/resonance_contributor_preference_rules.html)
"""

from itertools import combinations, product
import logging
from typing import List, Optional

from rdmc.resonance.exceptions import ResonanceError
from rdmc.resonance.utils import (
    get_order_str,
    get_charge_span,
    get_electronegativity,
    get_lone_pair,
    get_radical_count,
    get_total_bond_order,
    is_aromatic,
)
from rdmc.resonance.resonance import unset_aromatic_flags

from rdkit import Chem


logger = logging.getLogger(__name__)


# Pure RDKit
def filter_structures(
    mol_list,
    allow_expanded_octet: bool = True,
    features: Optional[list] = None,
    **kwargs,
):
    """
    This function filters them out by minimizing the number of C/N/O/S atoms without a full octet, non-preferred
    charge separation, and non-preferred aromatic structures.

    Args:
        mol_list (list): The list of molecules to filter.
        allow_expanded_octet (bool, optional): Whether to allow expanded octets for third row elements.
                                               Default is ``True``.
        features (list, optional): A list of features of the species. Default is ``None``.
        kwargs (dict, optional): Additional keyword arguments. They are ignored, but included for compatibility.
    """
    # Remove structures with different multiplicities generated
    # One identified issue is that Sanitize will make a N+ with two bond and 1 pair of electrons to
    # 2 radical electrons
    logger.debug(f"Filter_structures: {len(mol_list)} structures are fed in.")

    ref_radical_count = get_radical_count(mol_list[0])
    mol_list = [mol for mol in mol_list if get_radical_count(mol) == ref_radical_count]
    logger.debug(
        f"Filter_structures: {len(mol_list)} structures after removing ones with different multiplicities."
    )

    # Get an octet deviation list
    octet_deviation_list = get_octet_deviation_list(
        mol_list, allow_expanded_octet=allow_expanded_octet
    )

    # Filter mol_list using the octet rule and the respective octet deviation list
    filtered_list = octet_filtration(mol_list, octet_deviation_list)
    logger.debug(
        f"Filter_structures: {len(mol_list)} structures after octet filtration."
    )

    # Filter by charge
    filtered_list = charge_filtration(filtered_list)
    logger.debug(
        f"Filter_structures: {len(mol_list)} structures after charge filtration."
    )

    # Filter aromatic structures
    if features is not None and features["is_aromatic"]:
        filtered_list = aromaticity_filtration(filtered_list, features)
        logger.debug(
            f"Filter_structures: {len(mol_list)} structures after aromaticity filtration."
        )

    if not filtered_list:
        raise ResonanceError(
            f"Could not determine representative localized structures for species "
            f"{Chem.MolToSmiles(mol_list[0])}"
        )

    # Originally RMG checks reactivity here, it is removed since it is not used in RDMC

    # Make sure that the (first) original structure is always first in the list (unless it was filtered out).
    # Important whenever Species.molecule[0] is expected to be used (e.g., training reactions) after generating
    # resonance structures. However, if it was filtered out, it should be appended to the end of the list.
    for index, filtered in enumerate(filtered_list):
        if filtered.GetSubstructMatch(mol_list[0]):
            filtered_list.insert(0, filtered_list.pop(index))
            break
    else:
        # Append the original structure to list
        filtered_list.insert(0, mol_list[0])

    return filtered_list


# Pure RDKit
def get_octet_deviation_list(
    mol_list: list, allow_expanded_octet: bool = True
) -> List[float]:
    """
    Get the octet deviations for a list of molecules.

    Args:
        mol_list (list): The list of molecules to get the octet deviations for.
        allow_expanded_octet (bool, optional): Whether to allow expanded octets for third row elements.
                                               Default is ``True``.

    Returns:
        list: The octet deviations for the molecules in `mol_list`.
    """
    return [
        get_octet_deviation(mol, allow_expanded_octet=allow_expanded_octet)
        for mol in mol_list
    ]


# Pure RDKit
def get_octet_deviation(mol, allow_expanded_octet=True):
    """
    Returns the octet deviation for a :class:Molecule object
    if `allow_expanded_octet` is ``True`` (by default), then the function also considers dectet for
    third row elements (currently sulfur is the only hypervalance third row element in RMG)
    """
    # The overall "score" for the molecule, summed across all non-H atoms
    octet_deviation = 0
    for atom in mol.GetAtoms():
        atomic_num = atom.GetAtomicNum()
        if atomic_num == 1:
            continue
        num_lone_pair = get_lone_pair(atom)
        num_rad_elec = atom.GetNumRadicalElectrons()
        val_electrons = (
            2 * (int(get_total_bond_order(atom)) + num_lone_pair) + num_rad_elec
        )
        if atomic_num in [6, 7, 8]:
            # expecting C/N/O to be near octet
            octet_deviation += abs(8 - val_electrons)
        elif atomic_num == 16:
            if not allow_expanded_octet:
                # If allow_expanded_octet is False, then adhere to the octet rule for sulfur as well.
                # This is in accordance with J. Chem. Educ., 1995, 72 (7), p 583, DOI: 10.1021/ed072p583
                # This results in O=[:S+][:::O-] as a representative structure for SO2 rather than O=S=O,
                # and in C[:S+]([:::O-])C as a representative structure for DMSO rather than CS(=O)C.
                octet_deviation += abs(8 - val_electrons)
            else:
                # If allow_expanded_octet is True, then do not adhere to the octet rule for sulfur
                # and allow dectet structures (but don't prefer duedectet).
                # This is in accordance with:
                # -  J. Chem. Educ., 1972, 49 (12), p 819, DOI: 10.1021/ed049p819
                # -  J. Chem. Educ., 1986, 63 (1), p 28, DOI: 10.1021/ed063p28
                # -  J. Chem. Educ., 1992, 69 (10), p 791, DOI: 10.1021/ed069p791
                # -  J. Chem. Educ., 1999, 76 (7), p 1013, DOI: 10.1021/ed076p1013
                # This results in O=S=O as a representative structure for SO2 rather than O=[:S+][:::O-],
                # and in CS(=O)C as a representative structure for DMSO rather than C[:S+]([:::O-])C.
                if num_lone_pair <= 1:
                    octet_deviation += min(
                        abs(8 - val_electrons),
                        abs(10 - val_electrons),
                        abs(12 - val_electrons),
                    )  # octet/dectet on S p[0,1]
                    # eg [O-][S+]=O, O[S]=O, OS([O])=O, O=S(=O)(O)O
                elif num_lone_pair >= 2:
                    octet_deviation += abs(8 - val_electrons)  # octet on S p[2,3]
                    # eg [S][S], OS[O], [NH+]#[N+][S-][O-], O[S-](O)[N+]#N, S=[O+][O-]
            for bond in atom.GetBonds():
                atom2 = bond.GetOtherAtom(atom)
                if atom2.GetAtomicNum() == 16 and bond.GetBondType() == 3:
                    # penalty for S#S substructures. Often times sulfur can have a triple
                    # bond to another sulfur in a structure that obeys the octet rule, but probably shouldn't be a
                    # correct resonance structure. This adds to the combinatorial effect of resonance structures
                    # when generating reactions, yet probably isn't too important for reactivity. The penalty value
                    # is 0.5 since S#S substructures are captured twice (once for each S atom).
                    # Examples: CS(=O)SC <=> CS(=O)#SC;
                    # [O.]OSS[O.] <=> [O.]OS#S[O.] <=> [O.]OS#[S.]=O; N#[N+]SS[O-] <=> N#[N+]C#S[O-]
                    octet_deviation += 0.5
        # Penalize birad sites only if they theoretically substitute a lone pair.
        # E.g., O=[:S..] is penalized, but [C..]=C=O isn't.
        if num_rad_elec >= 2 and (
            (atomic_num == 7 and num_lone_pair == 0)
            or (atomic_num == 8 and num_lone_pair in [0, 1, 2])
            or (atomic_num == 16 and num_lone_pair in [0, 1, 2])
        ):
            octet_deviation += 3

    return octet_deviation


# Pure RDkit
def octet_filtration(mol_list, octet_deviation_list):
    """
    Returns a filtered list based on the octet_deviation_list. Also computes and returns a charge_span_list.
    Filtering using the octet deviation criterion rules out most unrepresentative structures. However, since some
    charge-strained species are still kept (e.g., [NH]N=S=O <-> [NH+]#[N+][S-][O-]), we also generate during the same
    loop a charge_span_list to keep track of the charge spans. This is used for further filtering.
    """
    min_octet_deviation = min(octet_deviation_list)
    return [
        mol
        for mol, octet_deviation in zip(mol_list, octet_deviation_list)
        if octet_deviation == min_octet_deviation
    ]


# Pure RDKit
def get_charge_span_list(mol_list: list) -> List[float]:
    """
    Get the list of charge spans for a list of molecules.
    This is also calculated in the octet_filtration() function along with the octet filtration process.

    Args:
        mol_list (list): The list of molecules to get the charge spans for.

    Returns:
        list: The charge spans for the molecules in `mol_list`.
    """
    return [get_charge_span(mol) for mol in mol_list]


# Pure RDKit
def charge_filtration(mol_list: list):
    """
    Returns a new filtered_list, filtered based on charge_span_list, electronegativity and proximity considerations.
    If structures with an additional charge layer introduce reactive sites (i.e., radicals or multiple bonds) they will
    also be considered. For example:

        - Both of NO2's resonance structures will be kept: [O]N=O <=> O=[N+.][O-]
        - NCO will only have two resonance structures [N.]=C=O <=> N#C[O.], and will loose the third structure which has
          the same octet deviation, has a charge separation, but the radical site has already been considered: [N+.]#C[O-]
        - CH2NO keeps all three structures, since a new radical site is introduced: [CH2.]N=O <=> C=N[O.] <=> C=[N+.][O-]
        - NH2CHO has two structures, one of which is charged since it introduces a multiple bond: NC=O <=> [NH2+]=C[O-]

    However, if the species is not a radical, or multiple bonds do not alter, we only keep the structures with the
    minimal charge span. For example:

        - NSH will only keep the N#S form and not [N-]=[SH+]
        - The following species will loose two thirds of its resonance structures, which are charged: CS(=O)SC <=>
          CS(=O)#SC <=> C[S+]([O-]SC <=> CS([O-])=[S+]C <=> C[S+]([O-])#SC <=> C[S+](=O)=[S-]C
        - Azide is know to have three resonance structures: [NH-][N+]#N <=> N=[N+]=[N-] <=> [NH+]#[N+][N-2];
          here we filter the third one out due to the higher charge span, which does not contribute to reactivity in RMG
    """
    charge_span_list = get_charge_span_list(mol_list)
    min_charge_span = min(charge_span_list)

    if min_charge_span == 0 and len(set(charge_span_list)) == 1:
        return mol_list
    elif len(set(charge_span_list)) > 1:
        # Proceed if there are structures with different charge spans
        extra_charged_list, filtered_list = [], []
        for mol, charge_span in zip(mol_list, charge_span_list):
            if charge_span == min_charge_span:
                # the minimal charge span layer
                filtered_list.append(mol)
            elif charge_span == min_charge_span + 1:
                # save the 2nd charge span layer
                extra_charged_list.append(mol)
    else:
        filtered_list = mol_list
        extra_charged_list = []

    # If the species has charge separation, apply charge stability considerations.
    # These considerations should be checked regardless of the existence of radical sites.
    filtered_list = stabilize_charges_by_electronegativity(filtered_list)
    filtered_list = stabilize_charges_by_proximity(filtered_list)

    if extra_charged_list:
        # Find the radical and multiple bond sites in all filtered_list structures
        # as the sorting labels for radical sites (atom1) and for multiple bond sites (atom1, atom2), respectively.
        rad_idxs, mul_bond_idxs = set(), set()
        for mol in filtered_list:
            for atom in mol.GetAtoms():
                if atom.GetNumRadicalElectrons():
                    rad_idxs.add(atom.GetIdx())
            for bond in mol.GetBonds():
                if bond.GetBondType() in [2, 3]:
                    mul_bond_idxs.add(
                        tuple(sorted((bond.GetBeginAtomIdx(), bond.GetEndAtomIdx())))
                    )
        # Find unique radical and multiple bond sites in charged_list and append to extra_charged_list:
        extra_charged_list = [
            mol
            for mol in extra_charged_list
            if has_unique_sites(mol, rad_idxs, mul_bond_idxs)
        ]

        if extra_charged_list:
            extra_charged_list = stabilize_charges_by_electronegativity(
                extra_charged_list, allow_empty_list=True
            )
            extra_charged_list = stabilize_charges_by_proximity(extra_charged_list)

    return filtered_list + extra_charged_list


# Pure RDKit
def has_unique_sites(
    mol,
    rad_idxs: set,
    mul_bond_idxs: set,
) -> bool:
    """
    Check if a resonance structure has unique radical and multiple bond sites that are not present in other structures.

    Args:
        mol (Mol or RDKitMol): The molecule to check.
        rad_idxs (set): The set of radical sites in the other structures.
        mul_bond_idxs (set): The set of multiple bond sites in the other structures.

    Returns:
        bool: ``True`` if the structure has unique radical and multiple bond sites, ``False`` otherwise.
    """
    for atom in mol.GetAtoms():
        if atom.GetNumRadicalElectrons() and atom.GetIdx() not in rad_idxs:
            return True
    for bond in atom.GetBonds():
        bond_idx = tuple(sorted((bond.GetBeginAtomIdx(), bond.GetEndAtomIdx())))
        if (
            (bond.GetBondType() in [2, 3])
            and bond_idx not in mul_bond_idxs
            and not (
                bond.GetBeginAtom().GetAtomicNum()
                == bond.GetEndAtom().GetAtomicNum()
                == 16
            )
        ):
            # We check that both atoms aren't S, otherwise we get [S.-]=[S.+] as a structure of S2 triplet
            return True
    return False


# Oxonium template for electronegativity considerations
template = Chem.MolFromSmarts("[O+X{1-3};!$([O+]-F)]")


# Pure RDKit
def stabilize_charges_by_electronegativity(
    mol_list: list,
    allow_empty_list: bool = False,
) -> list:
    """
    Only keep structures that obey the electronegativity rule. If a structure must have charge separation, negative
    charges will be assigned to more electronegative atoms, and vice versa.

    Args:
        mol_list (list): The list of molecules to filter.
        allow_empty_list (bool, optional): Whether to allow an empty list to be returned. Default is ``False``.
                                           If allow_empty_list is set to ``False``, and all structures in `mol_list` violate the
                                           electronegativity heuristic, this function will return the original ``mol_list``.
                                           (examples: [C-]#[O+], CS, [NH+]#[C-], [OH+]=[N-], [C-][S+]=C violate this heuristic).
    """
    mol_list_copy = []
    for mol in mol_list:
        X_positive = X_negative = 0
        for atom in mol.GetAtoms():
            charge = atom.GetFormalCharge()
            if charge > 0:
                X_positive += get_electronegativity(atom) * abs(charge)
            elif charge < 0:
                X_negative += get_electronegativity(atom) * abs(charge)
        # The following treatment is introduced in RMG
        # However, the condition is weird (asking for O-[F-] which is not valid)
        # The current implementation loosen the condition to [O+]-F and use substructure matching
        # The following is a comment from RMG along with the original code:
        # as in [N-2][N+]#[O+], [O-]S#[O+], OS(S)([O-])#[O+], [OH+]=S(O)(=O)[O-], [OH.+][S-]=O.
        # [C-]#[O+] and [O-][O+]=O, which are correct structures, also get penalized here, but that's OK
        # since they are still eventually selected as representative structures according to the rules here
        X_positive += len(mol.GetSubstructMatches(template))

        if X_positive <= X_negative:
            # Filter structures in which more electronegative atoms are positively charged.
            # This condition is NOT hermetic: It is possible to think of a situation where one structure has
            # several pairs of formally charged atoms, where one of the pairs isn't obeying the
            # electronegativity rule, while the sum of the pairs does.
            mol_list_copy.append(mol)

    if mol_list_copy or allow_empty_list:
        return mol_list_copy
    return mol_list


pos_atom_pattern = Chem.MolFromSmarts("[+]")
neg_atom_pattern = Chem.MolFromSmarts("[-]")


# Pure RDKit
def get_charge_distance(mol) -> tuple:
    """
    Get the cumulated charge distance for similar charge and difference charge pairs, respectively.

    Args:
        mol (Mol or RDKitMol): The molecule to check.
    """
    pos_atoms = [a[0] for a in mol.GetSubstructMatches(pos_atom_pattern)]
    neg_atoms = [a[0] for a in mol.GetSubstructMatches(neg_atom_pattern)]

    cumulative_similar_charge_distance = sum(
        [
            len(Chem.GetShortestPath(mol, a1, a2))
            for a1, a2 in combinations(pos_atoms, 2)
        ]
    )
    cumulative_similar_charge_distance += sum(
        [
            len(Chem.GetShortestPath(mol, a1, a2))
            for a1, a2 in combinations(neg_atoms, 2)
        ]
    )
    cumulative_opposite_charge_distance = sum(
        [
            len(Chem.GetShortestPath(mol, a1, a2))
            for a1, a2 in product(pos_atoms, neg_atoms)
        ]
    )
    return cumulative_opposite_charge_distance, cumulative_similar_charge_distance


# Pure RDKit
def stabilize_charges_by_proximity(mol_list: list) -> list:
    """
    Only keep structures that obey the charge proximity rule.
    Opposite charges will be as close as possible to one another, and vice versa.
    """
    charge_distance_list = [get_charge_distance(mol) for mol in mol_list]
    min_cumulative_opposite_charge_distance = min(
        [distances[0] for distances in charge_distance_list],
        default=0,
    )
    max_cumulative_similar_charge_distance = max(
        [distances[1] for distances in charge_distance_list],
        default=0,
    )
    return [
        mol
        for mol, charge_distance in zip(mol_list, charge_distance_list)
        if (charge_distance[0] <= min_cumulative_opposite_charge_distance)
        and (charge_distance[1] >= max_cumulative_similar_charge_distance)
    ]


# Pure RDKit
def aromaticity_filtration(
    mol_list: list,
    features: list,
) -> list:
    """
    Returns a filtered list of molecules based on heuristics for determining
    representative aromatic resonance structures.

    For monocyclic aromatics, Kekule structures are removed, with the
    assumption that an equivalent aromatic structure exists. Non-aromatic
    structures are maintained if they present new radical sites. Instead of
    explicitly checking the radical sites, we only check for the SDSDSD bond
    motif since radical delocalization will disrupt that pattern.

    For polycyclic aromatics, structures without any benzene bonds are removed.
    The idea is that radical delocalization into the aromatic pi system is
    unfavorable because it disrupts aromaticity. Therefore, structures where
    the radical is delocalized so far into the molecule such that none of the
    rings are aromatic anymore are not representative. While this isn't strictly
    true, it helps reduce the number of representative structures by focusing
    on the most important ones.
    """
    mol_list = [unset_aromatic_flags(mol) for mol in mol_list]
    # Start by selecting all aromatic resonance structures
    filtered_list = []
    other_list = []
    for mol in mol_list:
        if is_aromatic(mol):
            filtered_list.append(mol)
        else:
            other_list.append(mol)

    if not features["isPolycyclicAromatic"]:
        # Look for structures that don't have standard SDSDSD bond orders
        for mol in other_list:
            # Check all 6 membered rings
            # rings = [ring for ring in mol.get_relevant_cycles() if len(ring) == 6]
            # RDKit doesn't have a support to get all relevant cycles...
            # Temporarily use the BondRings as a rough fix
            # TODO: Implement pyrdl to get all relevant cycles which doesn't have full support
            # for different python versions and different OS
            # Another workaround maybe temporarily ignore polycyclic aromatics
            bond_lists = [
                ring for ring in mol.GetRingInfo().BondRings() if len(ring) == 6
            ]
            for bond_list in bond_lists:
                bond_orders = "".join(
                    [get_order_str(mol.GetBondWithIdx(bond)) for bond in bond_list]
                )
                if bond_orders == "SDSDSD" or bond_orders == "DSDSDS":
                    break
            else:
                filtered_list.append(mol)

    return filtered_list
