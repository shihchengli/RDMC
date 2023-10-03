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
This module contains methods for generation of resonance structures of molecules.

The main function to generate all relevant resonance structures for a given
Molecule object is ``generate_resonance_structures``. It calls the necessary
functions for generating each type of resonance structure.

Currently supported resonance types:

- All species:
    - ``generate_allyl_delocalization_resonance_structures``: single radical shift with double or triple bond
    - ``generate_lone_pair_multiple_bond_resonance_structures``: lone pair shift with double or triple bond in a 3-atom system (between nonadjacent atoms)
    - ``generate_adj_lone_pair_radical_resonance_structures``: single radical shift with lone pair between adjacent atoms
    - ``generate_adj_lone_pair_multiple_bond_resonance_structures``: multiple bond shift with lone pair between adjacent atoms
    - ``generate_adj_lone_pair_radical_multiple_bond_resonance_structures``: multiple bond and radical shift with lone pair and radical  between adjacent atoms
    - ``generate_N5dc_radical_resonance_structures``: shift between radical and lone pair mediated by an N5dc atom
    - ``generate_aryne_resonance_structures``: shift between cumulene and alkyne forms of arynes, which are not considered aromatic in RMG
- Aromatic species only:
    - ``generate_optimal_aromatic_resonance_structures``: fully delocalized structure, where all aromatic rings have benzene bonds
    - ``generate_kekule_structure``: generate a single Kekule structure for an aromatic compound (single/double bond form)
    - ``generate_opposite_kekule_structure``: for monocyclic aromatic species, rotate the double bond assignment
    - ``generate_clar_structures``: generate all structures with the maximum number of pi-sextet assignments
"""

import logging

from rdkit import Chem

import rdmc.resonance.filtration as filtration
import rdmc.resonance.pathfinder as pathfinder
from rdmc.resonance.utils import (decrement_radical,
                                  decrement_order,
                                  increment_radical,
                                  increment_order,
                                  is_aromatic,
                                  is_cyclic,
                                  is_identical,
                                  is_radical,
                                  is_aryl_radical,
                                  get_aromatic_rings,
                                  get_charge_span,
                                  get_lone_pair,
                                  get_order_str,
                                  get_relevant_cycles,
                                  update_charge)
from rdmc.resonance.resonance import _unset_aromatic_flags

# from rmgpy.exceptions import ILPSolutionError, KekulizationError, AtomTypeError, ResonanceError
# from rmgpy.molecule.adjlist import Saturator
# from rmgpy.molecule.graph import Vertex
# from rmgpy.molecule.kekulize import kekulize
# from rmgpy.molecule.molecule import Atom, Bond, Molecule


def populate_resonance_algorithms(features=None):
    """
    Generate list of resonance structure algorithms relevant to the current molecule.

    Takes a dictionary of features generated by analyze_molecule().
    Returns a list of resonance algorithms.
    """
    method_list = []

    if features is None:
        method_list = [
            generate_allyl_delocalization_resonance_structures,
            generate_lone_pair_multiple_bond_resonance_structures,
            generate_adj_lone_pair_radical_resonance_structures,
            generate_adj_lone_pair_multiple_bond_resonance_structures,
            generate_adj_lone_pair_radical_multiple_bond_resonance_structures,
            generate_N5dc_radical_resonance_structures,
            generate_optimal_aromatic_resonance_structures,
            generate_aryne_resonance_structures,
            generate_kekule_structure,
            generate_clar_structures,
        ]
    else:
        # If the molecule is aromatic, then radical resonance has already been considered
        # If the molecule was falsely identified as aromatic, then is_aryl_radical will still accurately capture
        # cases where the radical is in an orbital that is orthogonal to the pi orbitals.
        if features['is_radical'] and not features['is_aromatic'] and not features['is_aryl_radical']:
            method_list.append(generate_allyl_delocalization_resonance_structures)
        if features['is_cyclic']:
            method_list.append(generate_aryne_resonance_structures)
        if features['hasNitrogenVal5']:
            method_list.append(generate_N5dc_radical_resonance_structures)
        if features['hasLonePairs']:
            method_list.append(generate_adj_lone_pair_radical_resonance_structures)
            method_list.append(generate_adj_lone_pair_multiple_bond_resonance_structures)
            method_list.append(generate_adj_lone_pair_radical_multiple_bond_resonance_structures)
            if not features['is_aromatic']:
                # The generate_lone_pair_multiple_bond_resonance_structures method may perturb the electronic
                # configuration of a conjugated aromatic system, causing a major slow-down (two orders of magnitude
                # slower in one observed case), and it doesn't necessarily result in new representative localized
                # structures. Here we forbid it for all structures bearing at least one aromatic ring as a "good enough"
                # solution. A more holistic approach would be to identify these cases in generate_resonance_structures,
                # and pass a list of forbidden atom ID's to find_lone_pair_multiple_bond_paths.
                method_list.append(generate_lone_pair_multiple_bond_resonance_structures)

    return method_list


def analyze_molecule(mol):
    """
    Identify key features of molecule important for resonance structure generation.
    `save_order` is used to maintain the atom order, when analyzing the molecule, defaults to False.

    Returns a dictionary of features.
    """
    features = {'is_radical': is_radical(mol),
                'is_cyclic': is_cyclic(mol),
                'is_aromatic': False,
                'isPolycyclicAromatic': False,
                'is_aryl_radical': False,
                'hasNitrogenVal5': False,
                'hasLonePairs': False,
                }

    if features['is_cyclic']:
        aromatic_rings = get_aromatic_rings(mol)[0]
        if len(aromatic_rings) > 0:
            features['is_aromatic'] = True
        if len(aromatic_rings) > 1:
            features['isPolycyclicAromatic'] = True
        if features['is_radical'] and features['is_aromatic']:
            features['is_aryl_radical'] = is_aryl_radical(mol, aromatic_rings)
    for atom in mol.GetAtoms():
        if atom.GetAtomicNum() == 7 and get_lone_pair(atom) == 0:
            features['hasNitrogenVal5'] = True
        if get_lone_pair(atom) > 0:
            features['hasLonePairs'] = True

    return features


def generate_resonance_structures(mol, clar_structures=False, keep_isomorphic=False,
                                  filter_structures=True):
    """
    Generate and return all of the resonance structures for the input molecule.

    Most of the complexity of this method goes into handling aromatic species, particularly to generate an accurate
    set of resonance structures that is consistent regardless of the input structure. The following considerations
    are made:

    1. False positives from RDKit aromaticity detection can occur if a molecule has exocyclic double bonds
    2. False negatives from RDKit aromaticity detection can occur if a radical is delocalized into an aromatic ring
    3. sp2 hybridized radicals in the plane of an aromatic ring do not participate in hyperconjugation
    4. Non-aromatic resonance structures of PAHs are not important resonance contributors (assumption)

    Aromatic species are broken into the following categories for resonance treatment:

    - Radical polycyclic aromatic species: Kekule structures are generated in order to generate adjacent resonance
      structures. The resulting structures are then used for Clar structure generation. After all three steps, any
      non-aromatic structures are removed, under the assumption that they are not important resonance contributors.
    - Radical monocyclic aromatic species: Kekule structures are generated along with adjacent resonance structures.
      All are kept regardless of aromaticity because the radical is more likely to delocalize into the ring.
    - Stable polycyclic aromatic species: Clar structures are generated
    - Stable monocyclic aromatic species: Kekule structures are generated
    """
    # TODO: Clar_structure is not the first priority. will be implemented later.

    # Check that mol is a valid structure in terms of atomTypes and net charge. Since SMILES with hypervalance
    # heteroatoms are not always read correctly, print a suggestion to input the structure using an adjList.
    if mol.GetFormalCharge() != 0:
        # logging.info("Got the following structure:\nSMILES: {0}\nAdjacencyList:\n{1}\nNet charge: {2}\n\n"
        #                  "Currently RMG cannot process charged species correctly."
        #                  "\nIf this structure was entered in SMILES, try using the adjacencyList format for an"
        #                  " unambiguous definition. "
        #                  "Returning the input mol".format(mol.to_smiles(), mol.to_adjacency_list(), mol.get_net_charge()))
        return [mol]

    mol_list = [mol]

    # Analyze molecule
    features = analyze_molecule(mol)
    # Use generate_optimal_aromatic_resonance_structures to check for false positives and negatives
    if features['is_aromatic'] or (features['is_cyclic'] and features['is_radical'] and not features['is_aryl_radical']):
        new_mol_list = generate_optimal_aromatic_resonance_structures(mol, features)
        if len(new_mol_list) == 0:
            # Encountered false positive, ie. the molecule is not actually aromatic
            features['is_aromatic'] = False
            features['isPolycyclicAromatic'] = False
        else:
            features['is_aromatic'] = True
            if len(get_aromatic_rings(new_mol_list[0])[0]) > 1:
                features['isPolycyclicAromatic'] = True
            for new_mol in new_mol_list:
                # Append to structure list if unique
                if not keep_isomorphic and mol.GetSubstructMatch(new_mol):
                    # Note: `initial_map` and `generate_initial_map` is using default values.
                    # They are required in compilation before assigning `save_order`.
                    continue
                elif keep_isomorphic and is_identical(mol, new_mol):
                    continue
                else:
                    mol_list.append(new_mol)

    # Special handling for aromatic species
    if features['is_aromatic']:
        if features['is_radical'] and not features['is_aryl_radical']:
            _generate_resonance_structures(mol_list, [generate_kekule_structure],
                                           keep_isomorphic=keep_isomorphic)
            _generate_resonance_structures(mol_list, [generate_allyl_delocalization_resonance_structures],
                                           keep_isomorphic=keep_isomorphic)
        if features['isPolycyclicAromatic'] and clar_structures:
            _generate_resonance_structures(mol_list, [generate_clar_structures],
                                           keep_isomorphic=keep_isomorphic)
        else:
            _generate_resonance_structures(mol_list, [generate_aromatic_resonance_structure],
                                           keep_isomorphic=keep_isomorphic)

    # Generate remaining resonance structures
    method_list = populate_resonance_algorithms(features)
    _generate_resonance_structures(mol_list, method_list, keep_isomorphic=keep_isomorphic)

    if filter_structures:
        return filtration.filter_structures(mol_list, features=features)

    return mol_list


def _generate_resonance_structures(mol_list, method_list, keep_isomorphic=False, copy=False):
    """
    Iteratively generate all resonance structures for a list of starting molecules using the specified methods.

    Args:
        mol_list             starting list of molecules
        method_list          list of resonance structure algorithms
        keep_isomorphic      if False, removes any structures that give is_isomorphic=True (default)
                            if True, only remove structures that give is_identical=True
        copy                if False, append new resonance structures to input list (default)
                            if True, make a new list with all of the resonance structures
    """
    if copy:
        # Make a copy of the list so we don't modify the input list
        mol_list = mol_list[:]

    min_octet_deviation = min(filtration.get_octet_deviation_list(mol_list))
    min_charge_span = min(filtration.get_charge_span_list(mol_list))

    # Iterate over resonance structures
    index = 0
    while index < len(mol_list):
        molecule = mol_list[index]
        new_mol_list = []

        # On-the-fly filtration: Extend methods only for molecule that don't deviate too much from the octet rule
        # (a +2 distance from the minimal deviation is used, octet deviations per species are in increments of 2)
        # Sometimes rearranging the structure requires an additional higher charge span structure, so allow
        # structures with a +1 higher charge span compared to the minimum, e.g., [O-]S#S[N+]#N
        # Filtration is always called.
        octet_deviation = filtration.get_octet_deviation(molecule)
        charge_span = get_charge_span(molecule)
        if octet_deviation <= min_octet_deviation + 2 and charge_span <= min_charge_span + 1:
            for method in method_list:
                new_mol_list.extend(method(molecule))
            if octet_deviation < min_octet_deviation:
                # update min_octet_deviation to make this criterion tighter
                min_octet_deviation = octet_deviation
            if charge_span < min_charge_span:
                # update min_charge_span to make this criterion tighter
                min_charge_span = charge_span

        for new_mol in new_mol_list:
            # Append to structure list if unique
            for mol in mol_list:
                if not keep_isomorphic and mol.GetSubstructMatch(new_mol):
                    # Note: `initial_map` and `generate_initial_map` is using default values.
                    # They are required in compilation before assigning `save_order`.
                    break
                elif keep_isomorphic and is_identical(mol, new_mol):
                    break
            else:
                mol_list.append(new_mol)

        # Move to the next resonance structure
        index += 1

    # check net charge
    input_charge = mol_list[0].GetFormalCharge()

    for mol in mol_list[1:]:
        if mol.GetFormalCharge() != input_charge:
            mol_list.remove(mol)
            logging.debug(f'Resonance generation created a molecule {mol.ToSmiles(removeHs=False, removeAtomMap=False)}'
                          f'with a net charge of {mol.GetFormalCharge()} '
                          f'which does not match the input mol charge of {input_charge}.\n'
                          f'Removing it from resonance structures')

    return mol_list


def generate_allyl_delocalization_resonance_structures(mol):
    """
    Generate all of the resonance structures formed by one allyl radical shift.

    Biradicals on a single atom are not supported.
    """
    structures = []
    if not is_radical(mol):
        return structures

    # Iterate over radicals in structure
    for atom in mol.GetAtoms():
        paths = pathfinder.find_allyl_delocalization_paths(atom)
        for atom1_idx, _, atom3_idx, bond12_idx, bond23_idx in paths:
            try:
                # Make a copy of structure
                structure = mol.Copy(quickCopy=True)
                # Adjust to (potentially) new resonance structure
                decrement_radical(structure.GetAtomWithIdx(atom1_idx))
                increment_radical(structure.GetAtomWithIdx(atom3_idx))
                increment_order(structure.GetBondWithIdx(bond12_idx))
                decrement_order(structure.GetBondWithIdx(bond23_idx))
                structure.Sanitize(sanitizeOps=Chem.SanitizeFlags.SANITIZE_ALL ^ Chem.SanitizeFlags.SANITIZE_KEKULIZE)
            except BaseException as e:  # cannot make the change
                pass
            else:
                structures.append(structure)
    return structures


def generate_lone_pair_multiple_bond_resonance_structures(mol):
    """
    Generate all of the resonance structures formed by lone electron pair - multiple bond shifts in 3-atom systems.
    Examples: aniline (Nc1ccccc1), azide, [:NH2]C=[::O] <=> [NH2+]=C[:::O-]
    (where ':' denotes a lone pair, '.' denotes a radical, '-' not in [] denotes a single bond, '-'/'+' denote charge)
    """
    structures = []
    for atom in mol.GetAtoms():
        if get_lone_pair(atom) >= 1:
            paths = pathfinder.find_lone_pair_multiple_bond_paths(atom)
            for atom1_idx, _, atom3_idx, bond12_idx, bond23_idx in paths:
                try:
                    # Make a copy of structure
                    structure = mol.Copy(quickCopy=True)
                    # Adjust to (potentially) new resonance structure
                    atom1 = structure.GetAtomWithIdx(atom1_idx)
                    lone_pair1 = get_lone_pair(atom1)
                    if lone_pair1 <= 0:  # cannot decrease lone pair on atom1
                        continue
                    atom3 = structure.GetAtomWithIdx(atom3_idx)
                    lone_pair3 = get_lone_pair(atom3)
                    increment_order(structure.GetBondWithIdx(bond12_idx))
                    decrement_order(structure.GetBondWithIdx(bond23_idx))
                    update_charge(atom1, lone_pair1 - 1)
                    update_charge(atom3, lone_pair3 + 1)
                    structure.Sanitize(sanitizeOps=Chem.SanitizeFlags.SANITIZE_ALL ^ Chem.SanitizeFlags.SANITIZE_KEKULIZE)
                except BaseException as e:
                    pass # Don't append resonance structure if it creates an undefined atomtype
                else:
                    structures.append(structure)
    return structures


def generate_adj_lone_pair_radical_resonance_structures(mol):
    """
    Generate all of the resonance structures formed by lone electron pair - radical shifts between adjacent atoms.
    These resonance transformations do not involve changing bond orders.
    NO2 example: O=[:N]-[::O.] <=> O=[N.+]-[:::O-]
    (where ':' denotes a lone pair, '.' denotes a radical, '-' not in [] denotes a single bond, '-'/'+' denote charge)
    """
    structures = []
    if not is_radical(mol):
        return structures
    for atom in mol.GetAtoms():
        paths = pathfinder.find_adj_lone_pair_radical_delocalization_paths(atom)
        for atom1_idx, atom2_idx in paths:
            try:
                # Make a copy of structure
                structure = mol.Copy(quickCopy=True)
                # Adjust to (potentially) new resonance structure
                atom2 = structure.GetAtomWithIdx(atom2_idx)
                lone_pair2 = get_lone_pair(atom2)
                if lone_pair2 <= 0:
                    continue
                atom1 = structure.GetAtomWithIdx(atom1_idx)
                lone_pair1 = get_lone_pair(atom1)
                decrement_radical(atom1)
                increment_radical(atom2)
                update_charge(atom1, lone_pair1 + 1)
                update_charge(atom2, lone_pair2 - 1)
                structure.Sanitize(sanitizeOps=Chem.SanitizeFlags.SANITIZE_ALL ^ Chem.SanitizeFlags.SANITIZE_KEKULIZE)
            except BaseException as e:
                print(e) # Don't append resonance structure if it creates an undefined atomtype
            else:
                structures.append(structure)
    return structures


def generate_adj_lone_pair_multiple_bond_resonance_structures(mol):
    """
    Generate all of the resonance structures formed by lone electron pair - multiple bond shifts between adjacent atoms.
    Example: [:NH]=[CH2] <=> [::NH-]-[CH2+]
    (where ':' denotes a lone pair, '.' denotes a radical, '-' not in [] denotes a single bond, '-'/'+' denote charge)
    Here atom1 refers to the N/S/O atom, atom 2 refers to the any R!H (atom2's lone_pairs aren't affected)
    (In direction 1 atom1 <losses> a lone pair, in direction 2 atom1 <gains> a lone pair)
    """
    structures = []
    for atom in mol.GetAtoms():
        paths = pathfinder.find_adj_lone_pair_multiple_bond_delocalization_paths(atom)
        for atom1_idx, atom2_idx, bond12_idx, direction in paths:
            try:
                # Make a copy of structure
                structure = mol.Copy(quickCopy=True)
                atom1 = structure.GetAtomWithIdx(atom1_idx)
                atom2 = structure.GetAtomWithIdx(atom2_idx)
                lone_pair1 = get_lone_pair(atom1)
                bond12 = structure.GetBondWithIdx(bond12_idx)
                if direction == 1:  # The direction <increasing> the bond order
                    increment_order(bond12)
                    update_charge(atom1, lone_pair1 - 1)
                    atom1.decrement_lone_pairs()
                elif direction == 2:  # The direction <decreasing> the bond order
                    decrement_order(bond12)
                    update_charge(atom1, lone_pair1 + 1)
                lone_pair2 = get_lone_pair(atom2)
                atom2.update_charge(atom2, lone_pair2)
                structure.Sanitize(sanitizeOps=Chem.SanitizeFlags.SANITIZE_ALL ^ Chem.SanitizeFlags.SANITIZE_KEKULIZE)
            except BaseException as e:
                pass  # Don't append resonance structure if it creates an undefined atomtype
            else:
                if not structure.GetFormalCharge():
                    structures.append(structure)
    return structures


def generate_adj_lone_pair_radical_multiple_bond_resonance_structures(mol):
    """
    Generate all of the resonance structures formed by lone electron pair - radical - multiple bond shifts between adjacent atoms.
    Example: [:N.]=[CH2] <=> [::N]-[.CH2]
    (where ':' denotes a lone pair, '.' denotes a radical, '-' not in [] denotes a single bond, '-'/'+' denote charge)
    Here atom1 refers to the N/S/O atom, atom 2 refers to the any R!H (atom2's lone_pairs aren't affected)
    This function is similar to generate_adj_lone_pair_multiple_bond_resonance_structures() except for dealing with the
    radical transformations.
    (In direction 1 atom1 <losses> a lone pair, gains a radical, and atom2 looses a radical.
    In direction 2 atom1 <gains> a lone pair, looses a radical, and atom2 gains a radical)
    """
    structures = []
    if is_radical(mol):  # Iterate over radicals in structure
        for atom in mol.GetAtoms():
            paths = pathfinder.find_adj_lone_pair_radical_multiple_bond_delocalization_paths(atom)
            for atom1_idx, atom2_idx, bond12_idx, direction in paths:
                try:
                    # Make a copy of structure
                    structure = mol.Copy(quickCopy=True)
                    atom1, atom2 = structure.GetAtomWithIdx(atom1_idx), structure.GetAtomWithIdx(atom2_idx)
                    bond12 = structure.GetBondWithIdx(bond12_idx)
                    lone_pair_1 = get_lone_pair(atom1)
                    lone_pair_2 = get_lone_pair(atom2)
                    if direction == 1:  # The direction <increasing> the bond order
                        increment_order(bond12)
                        increment_radical(atom1)
                        update_charge(atom1, lone_pair_1 - 1)
                        decrement_radical(atom2)
                    elif direction == 2:  # The direction <decreasing> the bond order
                        decrement_order(bond12)
                        decrement_radical(atom1)
                        update_charge(atom1, lone_pair_1 + 1)
                        increment_radical(atom2)
                    update_charge(atom2, lone_pair_2)
                    structure.Sanitize(sanitizeOps=Chem.SanitizeFlags.SANITIZE_ALL ^ Chem.SanitizeFlags.SANITIZE_KEKULIZE)
                except BaseException as e:
                    pass  # Don't append resonance structure if it creates an undefined atomtype
                else:
                    structures.append(structure)
    return structures


def generate_N5dc_radical_resonance_structures(mol):
    """
    Generate all of the resonance structures formed by radical and lone pair shifts mediated by an N5dc atom.
    """
    structures = []
    for atom in mol.GetAtoms():
        if atom.GetAtomicNum == 5 and atom.GetFormalCharge() == 1 and atom.radical_electrons == 0 and len(atom.GetNeighbors()) == 3:
            paths = pathfinder.find_N5dc_radical_delocalization_paths(atom)
            for atom2_idx, atom3_idx in paths:
                try:
                    structure = mol.Copy(quickCopy=True)
                    atom2 = mol.GetAtomWithIdx(atom2_idx)
                    atom3 = mol.GetAtomWithIdx(atom3_idx)
                    lone_pair2 = get_lone_pair(atom2)
                    lone_pair3 = get_lone_pair(atom3)
                    decrement_radical(atom2)
                    increment_radical(atom3)
                    update_charge(atom2, lone_pair2 + 1)
                    update_charge(atom3, lone_pair3 - 1)
                except BaseException as e:
                    pass  # Don't append resonance structure if it creates an undefined atomtype
                else:
                    structures.append(structure)
    return structures


def generate_optimal_aromatic_resonance_structures(mol, features=None):
    """
    Generate the aromatic form of the molecule. For radicals, generates the form with the most aromatic rings.

    Returns result as a list.
    In most cases, only one structure will be returned.
    In certain cases where multiple forms have the same number of aromatic rings, multiple structures will be returned.
    If there's an error (eg. in RDKit) it just returns an empty list.
    """
    if features is None:
        features = analyze_molecule(mol)

    if not features['is_cyclic']:
        return []

    # Copy the molecule so we don't affect the original
    molecule = mol.Copy(quickCopy=True)

    # Attempt to rearrange electrons to obtain a structure with the most aromatic rings
    # Possible rearrangements include aryne resonance and allyl resonance
    res_list = [generate_aryne_resonance_structures]
    if features['is_radical'] and not features['is_aryl_radical']:
        res_list.append(generate_allyl_delocalization_resonance_structures)

    # if is_aromatic(molecule):
    #     kekule_list = generate_kekule_structure(molecule)
    # else:
    molecule.Sanitize(sanitizeOps=Chem.SanitizeFlags.SANITIZE_ALL)
    kekule_list = [molecule]

    _generate_resonance_structures(kekule_list, res_list)

    # Sort all of the generated structures by number of perceived aromatic rings
    mol_dict = {}
    for mol0 in kekule_list:
        aromatic_bonds = get_aromatic_rings(mol0)[1]
        num_aromatic = len(aromatic_bonds)
        mol_dict.setdefault(num_aromatic, []).append((mol0, aromatic_bonds))

    # List of potential number of aromatic rings, sorted from largest to smallest
    arom_options = sorted(mol_dict.keys(), reverse=True)

    new_mol_list = []
    for num in arom_options:
        mol_list = mol_dict[num]
        # Generate the aromatic resonance structure(s)
        for mol0, aromatic_bonds in mol_list:
            # Aromatize the molecule in place
            result = generate_aromatic_resonance_structure(mol0, aromatic_bonds, copy=False)
            if not result:
                # We failed to aromatize this molecule
                # This could be due to incorrect aromaticity perception by RDKit
                continue

            for mol1 in new_mol_list:
                if mol1.GetSubstructMatch(mol0):
                    # Note: `initial_map` and `generagenerate_initial_map` is using default values.
                    # They are required in compilation before assigning `save_order`.
                    break
            else:
                new_mol_list.append(mol0)

        if new_mol_list:
            # We found the most aromatic resonance structures so there's no need to try smaller numbers
            break

    return new_mol_list


def generate_aromatic_resonance_structure(mol, aromatic_bonds=None, copy=True):
    """
    Generate the aromatic form of the molecule in place without considering other resonance.

    Args:
        mol: :class:`Molecule` object to modify
        aromatic_bonds (optional): list of previously identified aromatic bonds
        copy (optional): copy the molecule if ``True``, otherwise modify in place

    Returns:
        List of one molecule if successful, empty list otherwise
    """
    if copy:
        molecule = mol.Copy(quickCopy=True)
    else:
        molecule = mol

    if aromatic_bonds is None:
        aromatic_bonds = get_aromatic_rings(molecule)[1]
    if len(aromatic_bonds) == 0:
        return []

    # Save original bond orders in case this doesn't work out
    original_bonds = []
    for ring in aromatic_bonds:
        original_order = []
        for bond in ring:
            original_order.append(bond.GetBondType())
        original_bonds.append(original_order)
    # Change bond types to benzene bonds for all aromatic rings
    for ring in aromatic_bonds:
        for bond in ring:
            bond.SetBondType(Chem.rdchem.BondType.AROMATIC)

    try:
        molecule.Sanitize(sanitizeOps=Chem.SanitizeFlags.SANITIZE_ALL ^ Chem.SanitizeFlags.SANITIZE_KEKULIZE)
    except:
        return []
    # except AtomTypeError:
    #     # If this didn't work the first time, then there might be a ring that is not actually aromatic
    #     # Reset our changes
    #     for ring, original_order in zip(aromatic_bonds, original_bonds):
    #         for bond, order in zip(ring, original_order):
    #             bond.order = order
    #     # Try to make each ring aromatic, one by one
    #     i = 0  # Track how many rings are aromatic
    #     counter = 0  # Track total number of attempts to avoid infinite loops
    #     while i < len(aromatic_bonds) and counter < 2 * len(aromatic_bonds):
    #         counter += 1
    #         original_order = []
    #         for bond in aromatic_bonds[i]:
    #             original_order.append(bond.order)
    #             bond.order = 1.5
    #         try:
    #             molecule.update_atomtypes(log_species=False)
    #         except AtomTypeError:
    #             # This ring could not be made aromatic, possibly because it depends on other rings
    #             # Undo changes
    #             for bond, order in zip(aromatic_bonds[i], original_order):
    #                 bond.order = order
    #             # Move it to the end of the list, and go on to the next ring
    #             aromatic_bonds.append(aromatic_bonds.pop(i))
    #             molecule.update_atomtypes(log_species=False)
    #             continue
    #         else:
    #             # We're done with this ring, so go on to the next ring
    #             i += 1
        # # If we didn't end up making any of the rings aromatic, then this molecule is not actually aromatic
        # if i == 0:
        #     # Move onto next molecule in the list
        #     return []

    return [molecule]


def generate_aryne_resonance_structures(mol):
    """
    Generate aryne resonance structures, including the cumulene and alkyne forms.

    For all 6-membered rings, check for the following bond patterns:

      - DDDSDS
      - STSDSD

    This does NOT cover all possible aryne resonance forms, only the simplest ones.
    Especially for polycyclic arynes, enumeration of all resonance forms is
    related to enumeration of all Kekule structures, which is very difficult.
    """
    rings = get_relevant_cycles(mol)
    rings = [ring for ring in rings if len(ring) == 6]

    new_mol_list = []
    for ring in rings:
        # Get bond orders
        bond_list = ring
        bond_orders = ''.join([get_order_str(mol.GetBondWithIdx(bond_idx)) for bond_idx in bond_list])
        new_orders = None
        # Check for expected bond patterns
        if bond_orders.count('T') == 1:
            # Reorder the list so the triple bond is first
            ind = bond_orders.index('T')
            bond_orders = bond_orders[ind:] + bond_orders[:ind]
            bond_list = bond_list[ind:] + bond_list[:ind]
            # Check for patterns
            if bond_orders == 'TSDSDS':
                new_orders = 'DDSDSD'
        elif bond_orders.count('D') == 4:
            # Search for DDD and reorder the list so that it comes first
            if 'DDD' in bond_orders:
                ind = bond_orders.index('DDD')
                bond_orders = bond_orders[ind:] + bond_orders[:ind]
                bond_list = bond_list[ind:] + bond_list[:ind]
            elif bond_orders.startswith('DD') and bond_orders.endswith('D'):
                bond_orders = bond_orders[-1:] + bond_orders[:-1]
                bond_list = bond_list[-1:] + bond_list[:-1]
            elif bond_orders.startswith('D') and bond_orders.endswith('DD'):
                bond_orders = bond_orders[-2:] + bond_orders[:-2]
                bond_list = bond_list[-2:] + bond_list[:-2]
            # Check for patterns
            if bond_orders == 'DDDSDS':
                new_orders = 'STSDSD'

        if new_orders is not None:
            # We matched one of our patterns, so we can now change the bonds
            # Make a copy of the molecule
            new_mol = mol.Copy(quickCopy=True)

            for i, bond in enumerate(bond_list):
                bond = new_mol.GetBondWithIdx(bond)
                if new_orders[i] == 'S':
                    bond.SetBondType(Chem.rdchem.BondType.SINGLE)
                elif new_orders[i] == 'D':
                    bond.SetBondType(Chem.rdchem.BondType.DOUBLE)
                elif new_orders[i] == 'T':
                    bond.SetBondType(Chem.rdchem.BondType.TRIPLE)

            try:
                new_mol.Sanitize(sanitizeOps=Chem.SanitizeFlags.SANITIZE_ALL ^ Chem.SanitizeFlags.SANITIZE_KEKULIZE)
            except BaseException:
                pass  # Don't append resonance structure if it creates an undefined atomtype
            else:
                new_mol_list.append(new_mol)

    return new_mol_list


def generate_kekule_structure(mol):
    """
    Generate a kekulized (single-double bond) form of the molecule.
    The specific arrangement of double bonds is non-deterministic, and depends on RDKit.

    Returns a single Kekule structure as an element of a list of length 1.
    If there's an error (eg. in RDKit) then it just returns an empty list.
    """
    for atom in mol.GetAtoms():
        if atom.GetIsAromatic():
            break
    else:
        return []

    molecule = mol.Copy(quickCopy=True)

    try:
        molecule.Kekulize()
    except BaseException:
        return []

    return [molecule]


def generate_isomorphic_resonance_structures(mol, saturate_h=False):
    """
    Select the resonance isomer that is isomorphic to the parameter isomer, with the lowest unpaired
    electrons descriptor.

    We generate over all resonance isomers (non-isomorphic as well as isomorphic) and retain isomorphic
    isomers.

    If `saturate_h` is `True`, then saturate `mol` with hydrogens before generating the resonance structures,
    and remove the hydrogens before returning `isomorphic_isomers`. This is useful when resonance structures are
    generated for molecules in which all hydrogens were intentionally removed as in generating augInChI. Otherwise,
    RMG will probably get many of the lone_pairs and partial charges in a molecule wrong.

    WIP: do not generate aromatic resonance isomers.
    """

    if saturate_h:  # Add explicit hydrogen atoms to complete structure if desired
        # Saturator.saturate(mol.GetAtoms())
        Chem.AddHs(mol.ToRWMol())

    isomorphic_isomers = [mol]  # resonance isomers that are isomorphic to the parameter isomer.

    isomers = [mol]

    # Iterate over resonance isomers
    index = 0
    while index < len(isomers):
        isomer = isomers[index]

        new_isomers = []
        for algo in populate_resonance_algorithms():
            new_isomers.extend(algo(isomer))

        for newIsomer in new_isomers:
            # Append to isomer list if unique
            for isom in isomers:
                if newIsomer.Copy(quickCopy=True).GetSubstructMatch(isom):
                    isomorphic_isomers.append(newIsomer)
                    break
            else:
                isomers.append(newIsomer)

        # Move to next resonance isomer
        index += 1

    if saturate_h:  # remove hydrogens before returning isomorphic_isomers
        for isomer in isomorphic_isomers:
            Chem.RemoveHs(isomer.ToRWMol())

    return isomorphic_isomers


def generate_clar_structures(mol):
    """
    Generate Clar structures for a given molecule.

    Returns a list of :class:`Molecule` objects corresponding to the Clar structures.
    """
    if not is_cyclic(mol):
        return []

    # Atom IDs are necessary in order to maintain consistent matrices between iterations

    try:
        output = _clar_optimization(mol)
    except BaseException:
        # The optimization algorithm did not work on the first iteration
        return []

    mol_list = []

    for new_mol, aromatic_rings, bonds, solution in output:

        # The solution includes a part corresponding to rings, y, and a part corresponding to bonds, x, using
        # nomenclature from the paper. In y, 1 means the ring as a sextet, 0 means it does not.
        # In x, 1 corresponds to a double bond, 0 either means a single bond or the bond is part of a sextet.
        y = solution[0:len(aromatic_rings)]
        x = solution[len(aromatic_rings):]

        # Apply results to molecule - double bond locations first
        for index, bond in enumerate(bonds):
            if x[index] == 0:
                bond.order = 1  # single
            elif x[index] == 1:
                bond.order = 2  # double
            else:
                raise ValueError('Unaccepted bond value {0} obtained from optimization.'.format(x[index]))

        # Then apply locations of aromatic sextets by converting to benzene bonds
        for index, ring in enumerate(aromatic_rings):
            if y[index] == 1:
                _clar_transformation(new_mol, ring)

        try:
            new_mol.update_atomtypes()
        except BaseException:
            pass
        else:
            mol_list.append(new_mol)

    return mol_list


def _clar_optimization(mol, constraints=None, max_num=None):
    """
    Implements linear programming algorithm for finding Clar structures. This algorithm maximizes the number
    of Clar sextets within the constraints of molecular geometry and atom valency.

    Returns a list of valid Clar solutions in the form of a tuple, with the following entries:
        [0] Molecule object
        [1] List of aromatic rings
        [2] List of bonds
        [3] Optimization solution

    The optimization solution is a list of boolean values with sextet assignments followed by double bond assignments,
    with indices corresponding to the list of aromatic rings and list of bonds, respectively.

    Method adapted from:
        Hansen, P.; Zheng, M. The Clar Number of a Benzenoid Hydrocarbon and Linear Programming.
            J. Math. Chem. 1994, 15 (1), 93-107.
    """
    # from lpsolve55 import lpsolve
    from scipy.optimize import milp

    # Make a copy of the molecule so we don't destroy the original
    molecule = mol.Copy(quickCopy=True)

    aromatic_rings = get_aromatic_rings(molecule)[0]
    aromatic_rings.sort(key=lambda x: sum([atom.id for atom in x]))

    if not aromatic_rings:
        return []

    # Get list of atoms that are in rings
    atoms = set()
    for ring in aromatic_rings:
        atoms.update(ring)
    atoms = sorted(atoms, key=lambda x: x.id)

    # Get list of bonds involving the ring atoms, ignoring bonds to hydrogen
    bonds = set()
    for atom in atoms:
        bonds.update([atom.bonds[key] for key in atom.bonds.keys() if key.is_non_hydrogen()])
    bonds = sorted(bonds, key=lambda x: (x.atom1.id, x.atom2.id))

    # Identify exocyclic bonds, and save their bond orders
    exo = []
    for bond in bonds:
        if bond.atom1 not in atoms or bond.atom2 not in atoms:
            if bond.is_double():
                exo.append(1)
            else:
                exo.append(0)
        else:
            exo.append(None)

    # Dimensions
    l = len(aromatic_rings)
    m = len(atoms)
    n = l + len(bonds)

    # Connectivity matrix which indicates which rings and bonds each atom is in
    # Part of equality constraint Ax=b
    a = []
    for atom in atoms:
        in_ring = [1 if atom in ring else 0 for ring in aromatic_rings]
        in_bond = [1 if atom in [bond.atom1, bond.atom2] else 0 for bond in bonds]
        a.append(in_ring + in_bond)

    # Objective vector for optimization: sextets have a weight of 1, double bonds have a weight of 0
    objective = [1] * l + [0] * len(bonds)

    # Solve LP problem using lpsolve
    lp = lpsolve('make_lp', m, n)               # initialize lp with constraint matrix with m rows and n columns
    lpsolve('set_verbose', lp, 2)               # reduce messages from lpsolve
    lpsolve('set_obj_fn', lp, objective)        # set objective function
    lpsolve('set_maxim', lp)                    # set solver to maximize objective
    lpsolve('set_mat', lp, a)                   # set left hand side to constraint matrix
    lpsolve('set_rh_vec', lp, [1] * m)          # set right hand side to 1 for all constraints
    for i in range(m):                          # set all constraints as equality constraints
        lpsolve('set_constr_type', lp, i + 1, '=')
    lpsolve('set_binary', lp, [True] * n)       # set all variables to be binary

    # Constrain values of exocyclic bonds, since we don't want to modify them
    for i in range(l, n):
        if exo[i - l] is not None:
            # NOTE: lpsolve indexes from 1, so the variable we're changing should be i + 1
            lpsolve('set_bounds', lp, i + 1, exo[i - l], exo[i - l])

    # Add constraints to problem if provided
    if constraints is not None:
        for constraint in constraints:
            try:
                lpsolve('add_constraint', lp, constraint[0], '<=', constraint[1])
            except Exception as e:
                logging.debug('Unable to add constraint: {0} <= {1}'.format(constraint[0], constraint[1]))
                logging.debug(mol.to_adjacency_list())
                if str(e) == 'invalid vector.':
                    raise ILPSolutionError('Unable to add constraint, likely due to '
                                           'inconsistent aromatic ring perception.')
                else:
                    raise

    status = lpsolve('solve', lp)
    obj_val, solution = lpsolve('get_solution', lp)[0:2]
    lpsolve('delete_lp', lp)  # Delete the LP problem to clear up memory

    # Check that optimization was successful
    if status != 0:
        raise ILPSolutionError('Optimization could not find a valid solution.')

    # Check that we the result contains at least one aromatic sextet
    if obj_val == 0:
        return []

    # Check that the solution contains the maximum number of sextets possible
    if max_num is None:
        max_num = obj_val  # This is the first solution, so the result should be an upper limit
    elif obj_val < max_num:
        raise ILPSolutionError('Optimization obtained a sub-optimal solution.')

    if any([x != 1 and x != 0 for x in solution]):
        raise ILPSolutionError('Optimization obtained a non-integer solution.')

    # Generate constraints based on the solution obtained
    y = solution[0:l]
    new_a = y + [0] * len(bonds)
    new_b = sum(y) - 1
    if constraints is not None:
        constraints.append((new_a, new_b))
    else:
        constraints = [(new_a, new_b)]

    # Run optimization with additional constraints
    try:
        inner_solutions = _clar_optimization(mol, constraints=constraints, max_num=max_num)
    except ILPSolutionError:
        inner_solutions = []

    return inner_solutions + [(molecule, aromatic_rings, bonds, solution)]


def _clar_transformation(mol, aromatic_ring):
    """
    Performs Clar transformation for given ring in a molecule, ie. conversion to aromatic sextet.

    Args:
        mol             a :class:`Molecule` object
        aromaticRing    a list of :class:`Atom` objects corresponding to an aromatic ring in mol

    This function directly modifies the input molecule and does not return anything.
    """
    for bond_idx in aromatic_ring:
        mol.GetBondWithIdx(bond_idx).SetBondType(Chem.rdchem.BondType.AROMATIC)
