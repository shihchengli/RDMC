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
This module provides functions for searching paths within a molecule.
The paths generally consist of alternating atoms and bonds.
"""

import itertools
from queue import Queue

# from rmgpy.molecule.molecule import Atom
from rmgpy.molecule.graph import Vertex, Edge

from rdkit import Chem
from rdkit.Chem import Atom, Bond

from rdmc.utils import PERIODIC_TABLE


def find_butadiene(start, end):
    """
    Search for a path between start and end atom that consists of
    alternating non-single and single bonds.

    Returns a list with atom and bond elements from start to end, or
    None if nothing was found.
    """

    q = Queue()  # FIFO queue of paths that need to be analyzed
    q.put([start])

    while not q.empty():
        path = q.get()
        # search for end atom among the neighbors of the terminal atom of the path:
        terminal = path[-1]
        assert isinstance(terminal, Atom)
        for bond34 in terminal.GetBonds():
            atom4 = bond34.GetOtherAtom(terminal)
            if atom4 == end and bond34.GetBondType() != 1:  # we have found the path we are looking for
                # add the final bond and atom and return
                path.append(bond34)
                path.append(atom4)
                return path
        else:  # none of the neighbors is the end atom.
            # Add a new allyl path and try again:
            new_paths = add_allyls(path)
            [q.put(p) if p else '' for p in new_paths]

    # Could not find a resonance path from start atom to end atom
    return None


def find_butadiene_end_with_charge(start):
    """
    Search for a (4-atom, 3-bond) path between start and end atom that consists of
    alternating non-single and single bonds and ends with a charged atom.

    Returns a list with atom and bond elements from start to end, or
    None if nothing was found.
    """

    q = Queue()  # FIFO queue of paths that need to be analyzed
    q.put([start])

    while not q.empty():
        path = q.get()
        # search for end atom among the neighbors of the terminal atom of the path:
        terminal = path[-1]
        assert isinstance(terminal, Atom)
        for bond34 in terminal.GetBonds():
            atom4 = bond34.GetOtherAtom(terminal)
            if atom4.GetFormalCharge() != 0 and bond34.GetBondType() != 1 and atom4 not in path:
                # we have found the path we are looking for
                # add the final bond and atom and return
                path.append(bond34)
                path.append(atom4)
                return path
        else:  # none of the neighbors is the end atom.
            # Add a new allyl path and try again:
            new_paths = add_allyls(path)
            [q.put(p) if p else '' for p in new_paths]

    # Could not find a resonance path from start atom to end atom
    return None


def find_allyl_end_with_charge(start):
    """
    Search for a (3-atom, 2-bond) path between start and end atom that consists of
    alternating non-single and single bonds and ends with a charged atom.

    Returns a list with atom and bond elements from start to end, or
    an empty list if nothing was found.
    """
    paths = []

    q = Queue()  # FIFO queue of paths that need to be analyzed
    unsaturated_bonds = add_unsaturated_bonds([start])

    if not unsaturated_bonds:
        return []

    [q.put(path) for path in unsaturated_bonds]

    while not q.empty():
        path = q.get()
        # search for end atom among the neighbors of the terminal atom of the path:
        terminal = path[-1]
        assert isinstance(terminal, Atom)

        path_copy = path[:]
        for bond23 in terminal.GetBonds():
            atom3 = bond23.GetOtherAtom(terminal)
            if atom3.GetFormalCharge() != 0 and atom3 not in path_copy:  # we have found the path we are looking for
                # add the final bond and atom and return
                path_copy_copy = path_copy[:]
                path_copy_copy.extend([bond23, atom3])
                paths.append(path_copy_copy)
        else:  # none of the neighbors is the end atom.
            # Add a new inverse allyl path and try again:
            new_paths = add_inverse_allyls(path)
            [q.put(p) if p else '' for p in new_paths]

    # Could not find a resonance path from start atom to end atom
    return paths


def find_shortest_path(start, end, path=None):
    path = path if path else []
    path = path + [start]
    if start == end:
        return path

    shortest = None
    for node in start.edges.keys():
        if node not in path:
            newpath = find_shortest_path(node, end, path)
            if newpath:
                if not shortest or len(newpath) < len(shortest):
                    shortest = newpath
    return shortest


def add_unsaturated_bonds(path):
    """
    Find all the (2-atom, 1-bond) patterns "X=X" starting from the
    last atom of the existing path.

    The bond attached to the starting atom should be non single.
    """
    paths = []
    start = path[-1]
    assert isinstance(start, Atom)

    for bond12 in start.GetBonds():
        atom2 = bond12.GetOtherAtom(start)
        if bond12.GetBondType() != 1 and atom2 not in path and atom2.GetAtomicNum() != 1:
            new_path = path[:]
            new_path.extend((bond12, atom2))
            paths.append(new_path)
    return paths


def add_allyls(path):
    """
    Find all the (3-atom, 2-bond) patterns "X=X-X" starting from the
    last atom of the existing path.

    The bond attached to the starting atom should be non single.
    The second bond should be single.
    """
    paths = []
    start = path[-1]
    assert isinstance(start, Atom)

    for bond12 in start.GetBonds():
        atom2 = bond12.GetOtherAtom(start)
        if bond12.GetBondType() != 1 and atom2 not in path:
            for bond23 in atom2.GetBonds():
                atom3 = bond23.GetOtherAtom(atom2)
                if start is not atom3 and atom3.GetAtomicNum() != 1:
                    new_path = path[:]
                    new_path.extend((bond12, atom2, bond23, atom3))
                    paths.append(new_path)
    return paths


def add_inverse_allyls(path):
    """
    Find all the (3-atom, 2-bond) patterns "start~atom2=atom3" starting from the
    last atom of the existing path.

    The second bond should be non-single.
    """
    paths = []
    start = path[-1]
    assert isinstance(start, Atom)

    for bond12 in start.GetBonds():
        atom2 = bond12.GetOtherAtom(start)
        if atom2 not in path:
            for bond23 in atom2.GetBonds():
                atom3 = bond23.GetOtherAtom(atom2)
                if atom3 not in path and atom3.GetAtomicNum() != 1 and bond23.GetBondType() != 1:
                    new_path = path[:]
                    new_path.extend((bond12, atom2, bond23, atom3))
                    paths.append(new_path)
    return paths


def compute_atom_distance(atom_indices, mol):
    """
    Compute the distances between each pair of atoms in the atom_indices.

    The distance between two atoms is defined as the length of the shortest path
    between the two atoms minus 1, because the start atom is part of the path.

    The distance between multiple atoms is defined by generating all possible
    combinations between two atoms and storing the distance between each combination
    of atoms in a dictionary.

    The parameter 'atom_indices' is a  list of 1-based atom indices.

    """
    if len(atom_indices) == 1:
        return {(atom_indices[0],): 0}

    distances = {}
    combos = [sorted(tup) for tup in itertools.combinations(atom_indices, 2)]

    for i1, i2 in combos:
        start, end = mol.GetAtomWithIdx(i1 - 1), mol.GetAtomWithIdx(i2 - 1)
        path = Chem.rdmolops.GetShortestPath(mol, start, end)
        distances[(i1, i2)] = len(path) - 1

    return distances


def find_allyl_delocalization_paths(atom1):
    """
    Find all the delocalization paths allyl to the radical center indicated by `atom1`.
    """
    # No paths if atom1 is not a radical
    if atom1.GetNumRadicalElectrons() <= 0:
        return []

    paths = []
    for bond12 in atom1.GetBonds():
        atom2 = bond12.GetOtherAtom(atom1)
        if bond12.GetBondType() == 1 or bond12.GetBondType() == 2:
            for bond23 in atom2.GetBonds():
                atom3 = bond23.GetOtherAtom(atom2)
                # Allyl bond must be capable of losing an order without breaking
                if atom1 is not atom3 and (bond23.GetBondType() == 2 or bond23.GetBondType() == 3):
                    paths.append([atom1, atom2, atom3, bond12, bond23])
    return paths


def find_lone_pair_multiple_bond_paths(atom1):
    """
    Find all the delocalization paths between lone electron pair and multiple bond in a 3-atom system
    `atom1` indicates the localized lone pair site. Currently carbenes are excluded from this path.

    Examples:

    - N2O (N#[N+][O-] <-> [N-]=[N+]=O)
    - Azide (N#[N+][NH-] <-> [N-]=[N+]=N <-> [N-2][N+]#[NH+])
    - N#N group on sulfur (O[S-](O)[N+]#N <-> OS(O)=[N+]=[N-] <-> O[S+](O)#[N+][N-2])
    - N[N+]([O-])=O <=> N[N+](=O)[O-], these structures are isomorphic but not identical, this transition is
      important for correct degeneracy calculations
    """
    # No paths if atom1 has no lone pairs, or cannot lose them, or is a carbon atom
    if get_lone_pair(atom1) <= 0 or not is_atom_able_to_lose_lone_pair(atom1) or atom1.GetAtomicNum() == 6:
        return []

    paths = []
    for bond12 in atom1.GetBonds():
        atom2 = bond12.GetOtherAtom(atom1)
        # If both atom1 and atom2 are sulfur then don't do this type of resonance. Also, the bond must be capable of gaining an order.
        if (atom1.GetAtomicNum() != 16 or atom2.GetAtomicNum() != 16) and (bond12.GetBondType() == 1 or bond12.GetBondType() == 2):
            for bond23 in atom2.GetBonds():
                atom3 = bond23.GetOtherAtom(atom2)
                # Bond must be capable of losing an order without breaking, atom3 must be able to gain a lone pair
                if atom1 is not atom3 and (bond23.GetBondType() == 2 or bond23.GetBondType() == 3) \
                        and (atom3.GetAtomicNum() == 6 or is_atom_able_to_gain_lone_pair(atom3)):
                    paths.append([atom1, atom2, atom3, bond12, bond23])
    return paths


def find_adj_lone_pair_radical_delocalization_paths(atom1):
    """
    Find all the delocalization paths of lone electron pairs next to the radical center indicated
    by `atom1`. Used to generate resonance isomers in adjacent N/O/S atoms.
    Two adjacent O atoms are not allowed since (a) currently RMG has no good thermo/kinetics for R[:O+.][:::O-] which
    could have been generated as a resonance structure of R[::O][::O.].

    The radical site (atom1) could be either:

    - `N u1 p0`, eg O=[N.+][:::O-]
    - `N u1 p1`, eg R[:NH][:NH.]
    - `O u1 p1`, eg [:O.+]=[::N-]; not allowed when adjacent to another O atom
    - `O u1 p2`, eg O=N[::O.]; not allowed when adjacent to another O atom
    - `S u1 p0`, eg O[S.+]([O-])=O
    - `S u1 p1`, eg O[:S.+][O-]
    - `S u1 p2`, eg O=N[::S.]
    - any of the above with more than 1 radical where possible

    The non-radical site (atom2) could respectively be:

    - `N u0 p1`
    - `N u0 p2`
    - `O u0 p2`
    - `O u0 p3`
    - `S u0 p1`
    - `S u0 p2`
    - `S u0 p3`

    (where ':' denotes a lone pair, '.' denotes a radical, '-' not in [] denotes a single bond, '-'/'+' denote charge)
    The bond between the sites does not have to be single, e.g.: [:O.+]=[::N-] <=> [::O]=[:N.]
    """
    paths = []
    if (atom1.GetNumRadicalElectrons() >= 1) \
            and ((atom1.GetAtomicNum() == 6 and get_lone_pair(atom1) == 0)
                 or (atom1.GetAtomicNum() == 7 and get_lone_pair(atom1) in [0, 1])
                 or (atom1.GetAtomicNum() == 8 and get_lone_pair(atom1) in [1, 2])
                 or (atom1.GetAtomicNum() == 16 and get_lone_pair(atom1) in [0, 1, 2])):
        for atom2 in atom1.GetNeighbors():
            if ((atom2.GetAtomicNum() == 6 and get_lone_pair(atom2) == 1)
                    or (atom2.GetAtomicNum() == 7 and get_lone_pair(atom2) in [1, 2])
                    or (atom2.GetAtomicNum() == 8 and get_lone_pair(atom2) in [2, 3] and atom1.GetAtomicNum() != 6)
                    or (atom2.GetAtomicNum() == 16 and get_lone_pair(atom2) in [1, 2, 3])):
                paths.append([atom1, atom2])
    return paths


def find_adj_lone_pair_multiple_bond_delocalization_paths(atom1):
    """
    Find all the delocalization paths of atom1 which either

    - Has a lonePair and is bonded by a single/double bond (e.g., [::NH-]-[CH2+], [::N-]=[CH+]) -- direction 1
    - Can obtain a lonePair and is bonded by a double/triple bond (e.g., [:NH]=[CH2], [:N]#[CH]) -- direction 2

    Giving the following resonance transitions, for example:

    - [::NH-]-[CH2+] <=> [:NH]=[CH2]
    - [:N]#[CH] <=> [::N-]=[CH+]
    - other examples: S#N, N#[S], O=S([O])=O

    Direction "1" is the direction <increasing> the bond order as in [::NH-]-[CH2+] <=> [:NH]=[CH2]
    Direction "2" is the direction <decreasing> the bond order as in [:NH]=[CH2] <=> [::NH-]-[CH2+]
    (where ':' denotes a lone pair, '.' denotes a radical, '-' not in [] denotes a single bond, '-'/'+' denote charge)
    (In direction 1 atom1 <losses> a lone pair, in direction 2 atom1 <gains> a lone pair)
    """
    paths = []

    # Carbenes are currently excluded from this path.
    # Only atom1 is checked since it is either the donor or acceptor of the lone pair
    if atom1.GetAtomicNum() == 6:
        return paths

    for bond12 in atom1.GetBonds():
        atom2 = bond12.GetOtherAtom(atom1)
        if atom2.GetAtomicNum() > 1:  # don't bother with hydrogen atoms.
            # Find paths in the direction <increasing> the bond order,
            # atom1 must posses at least one lone pair to loose it
            # the final clause of this prevents S#S from forming by this resonance pathway
            if ((bond12.GetBondType() == 1 or bond12.GetBondType() == 2)
                    and is_atom_able_to_lose_lone_pair(atom1)) \
                    and not (atom1.GetAtomicNum() == 16
                             and atom2.GetAtomicNum() == 16
                             and bond12.GetBondType() == 2):
                paths.append([atom1, atom2, bond12, 1])  # direction = 1
            # Find paths in the direction <decreasing> the bond order,
            # atom1 gains a lone pair, hence cannot already have more than two lone pairs
            if ((bond12.GetBondType() == 2 or bond12.GetBondType() == 3)
                    and is_atom_able_to_gain_lone_pair(atom1)):
                paths.append([atom1, atom2, bond12, 2])  # direction = 2
    return paths


def find_adj_lone_pair_radical_multiple_bond_delocalization_paths(atom1):
    """
    Find all the delocalization paths of atom1 which either

    - Has a lonePair and is bonded by a single/double bond to a radical atom (e.g., [::N]-[.CH2])
    - Can obtain a lonePair, has a radical, and is bonded by a double/triple bond (e.g., [:N.]=[CH2])

    Giving the following resonance transitions, for example:

    - [::N]-[.CH2] <=> [:N.]=[CH2]
    - O[:S](=O)[::O.] <=> O[S.](=O)=[::O]

    Direction "1" is the direction <increasing> the bond order as in [::N]-[.CH2] <=> [:N.]=[CH2]
    Direction "2" is the direction <decreasing> the bond order as in [:N.]=[CH2] <=> [::N]-[.CH2]
    (where ':' denotes a lone pair, '.' denotes a radical, '-' not in [] denotes a single bond, '-'/'+' denote charge)
    (In direction 1 atom1 <losses> a lone pair, gains a radical, and atom2 looses a radical.
    In direction 2 atom1 <gains> a lone pair, looses a radical, and atom2 gains a radical)
    """
    paths = []

    # Carbenes are currently excluded from this path.
    # Only atom1 is checked since it is either the donor or acceptor of the lone pair
    if atom1.GetAtomicNum() == 6:
        return paths

    for bond12 in atom1.GetBonds():
        atom2 = bond12.GetOtherAtom(atom1)
        # Find paths in the direction <increasing> the bond order
        # atom1 must posses at least one lone pair to loose it, atom2 must be a radical
        if (atom2.GetNumRadicalElectrons() and (bond12.GetBondType() == 1 or bond12.GetBondType() == 2)
                and is_atom_able_to_lose_lone_pair(atom1)):
            paths.append([atom1, atom2, bond12, 1])  # direction = 1
        # Find paths in the direction <decreasing> the bond order
        # atom1 gains a lone pair, hence cannot already have more than two lone pairs, and is also a radical
        if (atom1.GetNumRadicalElectrons() and (bond12.GetBondType() == 2 or bond12.GetBondType() == 3)
                and is_atom_able_to_gain_lone_pair(atom1)):
            paths.append([atom1, atom2, bond12, 2])  # direction = 2
    return paths


def find_N5dc_radical_delocalization_paths(atom1):
    """
    Find all the resonance structures of an N5dc nitrogen atom with a single bond to a radical N/O/S site, another
    single bond to a negatively charged N/O/S site, and one double bond (not participating in this transformation)

    Example:

    - N=[N+]([O])([O-]) <=> N=[N+]([O-])([O]), these structures are isomorphic but not identical, the transition is
      important for correct degeneracy calculations

    In this transition atom1 is the middle N+ (N5dc), atom2 is the radical site, and atom3 is negatively charged
    A "if atom1.atomtype.label == 'N5dc'" check should be done before calling this function
    """
    path = []

    for bond12 in atom1.GetBonds():
        atom2 = bond12.GetOtherAtom(atom1)

        if atom2.GetNumRadicalElectrons() and bond12.GetBondType() == 1 and not atom2.GetFormalCharge() and is_atom_able_to_lose_lone_pair(atom2):
            for bond13 in atom1.GetBonds():
                atom3 = bond13.GetOtherAtom(atom1)
                if (atom2 is not atom3 and bond13.GetBondType() == 1 and atom3.GetFormalCharge() < 0
                        and is_atom_able_to_lose_lone_pair(atom3)):
                    path.append([atom2, atom3])
                    return path  # there could only be one such path per atom1, return if found
    return path


def is_atom_able_to_gain_lone_pair(atom):
    """
    Helper function
    Returns True if atom is N/O/S and is able to <gain> an additional lone pair, False otherwise
    We don't allow O to remain with no lone pairs
    """
    return (((atom.GetAtomicNum() == 7 or atom.GetAtomicNum() == 16) and get_lone_pair(atom) in [0, 1, 2])
            or (atom.GetAtomicNum() == 8 and get_lone_pair(atom) in [1, 2])
            or atom.GetAtomicNum() == 6 and get_lone_pair(atom) == 0)


def is_atom_able_to_lose_lone_pair(atom):
    """
    Helper function
    Returns True if atom is N/O/S and is able to <loose> a lone pair, False otherwise
    We don't allow O to remain with no lone pairs
    """
    return (((atom.GetAtomicNum() == 7 or atom.GetAtomicNum() == 16) and get_lone_pair(atom) in [1, 2, 3])
            or (atom.GetAtomicNum() == 8 and get_lone_pair(atom) in [2, 3])
            or atom.GetAtomicNum() == 6 and get_lone_pair(atom) == 1)


def get_lone_pair(atom):
    """
    Helper function
    Returns the lone pair of an atom
    """
    atomic_num = atom.GetAtomicNum()
    if atomic_num == 1:
        return 0
    order = int(sum([b.GetBondTypeAsDouble() for b in atom.GetBonds()]))
    return (PERIODIC_TABLE.GetNOuterElecs(atomic_num) - atom.GetNumRadicalElectrons() - atom.GetFormalCharge() - order) / 2
