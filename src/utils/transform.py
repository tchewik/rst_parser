# -*- coding: utf-8 -*-

from collections.abc import Iterable

import nltk, re
from src.utils.logging import get_logger, progress_bar
from src.utils.util_discourse import RelationAndNucleus2Label, Label2RelationAndNucleus
from src.utils.util_spmrl import custom_chomsky_normal_form, clean_leaves

logger = get_logger(__name__)
from copy import deepcopy


class Transform(object):
    """
    A Transform object corresponds to a specific data format.
    It holds several instances of data fields that provide instructions for preprocessing and numericalizing, etc.

    Attributes:
        training (bool):
            Sets the object in training mode.
            If ``False``, some data fields not required for predictions won't be returned.
            Default: ``True``.
    """

    fields = []

    def __init__(self):
        self.training = True

    def __call__(self, sentences):
        pairs = dict()
        for field in self:
            if field not in self.src and field not in self.tgt:
                continue
            # if not self.training and field in self.tgt:
            #     continue
            if not isinstance(field, Iterable):
                field = [field]
            for f in field:
                if f is not None:
                    pairs[f] = f.transform([getattr(i, f.name) for i in sentences])

        return pairs

    def __getitem__(self, index):
        return getattr(self, self.fields[index])

    def train(self, training=True):
        self.training = training

    def eval(self):
        self.train(False)

    def append(self, field):
        self.fields.append(field.name)
        setattr(self, field.name, field)

    @property
    def src(self):
        raise AttributeError

    @property
    def tgt(self):
        raise AttributeError

    def save(self, path, sentences):
        with open(path, 'w') as f:
            f.write('\n'.join([str(i) for i in sentences]) + '\n')


class Sentence(object):
    """
    A Sentence object holds a sentence with regard to specific data format.
    """

    def __init__(self, transform):
        self.transform = transform

        # mapping from each nested field to their proper position
        self.maps = dict()
        # names of each field
        self.keys = set()
        # values of each position
        self.values = []
        for i, field in enumerate(self.transform):
            if not isinstance(field, Iterable):
                field = [field]
            for f in field:
                if f is not None:
                    self.maps[f.name] = i
                    self.keys.add(f.name)

    def __len__(self):
        return len(self.values[0])

    def __contains__(self, key):
        return key in self.keys

    def __getattr__(self, name):
        if name in self.__dict__:
            return self.__dict__[name]
        else:
            return self.values[self.maps[name]]

    def __setattr__(self, name, value):
        if 'keys' in self.__dict__ and name in self:
            index = self.maps[name]
            if index >= len(self.values):
                self.__dict__[name] = value
            else:
                self.values[index] = value
        else:
            self.__dict__[name] = value

    def __getstate__(self):
        return vars(self)

    def __setstate__(self, state):
        self.__dict__.update(state)


class CoNLL(Transform):
    """
    The CoNLL object holds ten fields required for CoNLL-X data format.
    Each field is binded with one or more :class:`Field` objects. For example,
    ``FORM`` can contain both :class:`Field` and :class:`SubwordField` to produce tensors for words and subwords.

    Attributes:
        ID:
            Token counter, starting at 1.
        FORM:
            Words in the sentence.
        LEMMA:
            Lemmas or stems (depending on the particular treebank) of words, or underscores if not available.
        CPOS:
            Coarse-grained part-of-speech tags, where the tagset depends on the treebank.
        POS:
            Fine-grained part-of-speech tags, where the tagset depends on the treebank.
        FEATS:
            Unordered set of syntactic and/or morphological features (depending on the particular treebank),
            or underscores if not available.
        HEAD:
            Heads of the tokens, which are either values of ID or zeros.
        DEPREL:
            Dependency relations to the HEAD.
        PHEAD:
            Projective heads of tokens, which are either values of ID or zeros, or underscores if not available.
        PDEPREL:
            Dependency relations to the PHEAD, or underscores if not available.

    References:
        - Sabine Buchholz and Erwin Marsi. 2006.
          `CoNLL-X Shared Task on Multilingual Dependency Parsing`_.

    .. _CoNLL-X Shared Task on Multilingual Dependency Parsing:
        https://www.aclweb.org/anthology/W06-2920/
    """

    fields = ['ID', 'FORM', 'LEMMA', 'CPOS', 'POS', 'FEATS', 'HEAD', 'DEPREL', 'PHEAD', 'PDEPREL']

    def __init__(self,
                 ID=None, FORM=None, LEMMA=None, CPOS=None, POS=None,
                 FEATS=None, HEAD=None, DEPREL=None, PHEAD=None, PDEPREL=None):
        super().__init__()

        self.ID = ID
        self.FORM = FORM
        self.LEMMA = LEMMA
        self.CPOS = CPOS
        self.POS = POS
        self.FEATS = FEATS
        self.HEAD = HEAD
        self.DEPREL = DEPREL
        self.PHEAD = PHEAD
        self.PDEPREL = PDEPREL

    @property
    def src(self):
        return self.FORM, self.CPOS

    @property
    def tgt(self):
        return self.HEAD, self.DEPREL

    @classmethod
    def get_arcs(cls, sequence):
        return [int(i) for i in sequence]

    @classmethod
    def get_sibs(cls, sequence):
        sibs = [-1] * (len(sequence) + 1)
        heads = [0] + [int(i) for i in sequence]

        for i in range(1, len(heads)):
            hi = heads[i]
            for j in range(i + 1, len(heads)):
                hj = heads[j]
                di, dj = hi - i, hj - j
                if hi >= 0 and hj >= 0 and hi == hj and di * dj > 0:
                    if abs(di) > abs(dj):
                        sibs[i] = j
                    else:
                        sibs[j] = i
                    break
        return sibs[1:]

    @classmethod
    def toconll(cls, tokens):
        """
        Convert a list of tokens to a string in CoNLL-X format.
        Missing fields are filled with underscores.

        Args:
            tokens (list[str] or list[tuple]):
                This can be either a list of words or word/pos pairs.

        Returns:
            a string in CoNLL-X format.

        Examples:
            >>> print(CoNLL.toconll(['She', 'enjoys', 'playing', 'tennis', '.']))
            1       She     _       _       _       _       _       _       _       _
            2       enjoys  _       _       _       _       _       _       _       _
            3       playing _       _       _       _       _       _       _       _
            4       tennis  _       _       _       _       _       _       _       _
            5       .       _       _       _       _       _       _       _       _

        """

        if isinstance(tokens[0], str):
            s = '\n'.join([f"{i}\t{word}\t" + '\t'.join(['_'] * 8)
                           for i, word in enumerate(tokens, 1)])
        else:
            s = '\n'.join([f"{i}\t{word}\t_\t{tag}\t" + '\t'.join(['_'] * 6)
                           for i, (word, tag) in enumerate(tokens, 1)])
        return s + '\n'

    @classmethod
    def isprojective(cls, sequence):
        """
        Check if the dependency tree is projective.
        This also works for partial annotation.

        Besides the obvious crossing arcs, the examples below illustrate two non-projective cases
        that are hard to detect in the scenario of partial annotation.

        Args:
            sequence (list[int]):
                A list of head indices.

        Returns:
            ``True`` if the tree is projective, ``False`` otherwise.

        Examples:
            >>> CoNLL.isprojective([2, -1, 1])  # -1 denotes un-annotated cases
            False
            >>> CoNLL.isprojective([3, -1, 2])
            False
        """

        pairs = [(h, d) for d, h in enumerate(sequence, 1) if h >= 0]
        for i, (hi, di) in enumerate(pairs):
            for hj, dj in pairs[i + 1:]:
                (li, ri), (lj, rj) = sorted([hi, di]), sorted([hj, dj])
                if li <= hj <= ri and hi == dj:
                    return False
                if lj <= hi <= rj and hj == di:
                    return False
                if (li < lj < ri or li < rj < ri) and (li - lj) * (ri - rj) > 0:
                    return False
        return True

    @classmethod
    def istree(cls, sequence, proj=False, multiroot=False):
        """
        Check if the arcs form an valid dependency tree.

        Args:
            sequence (list[int]):
                A list of head indices.
            proj (bool):
                If ``True``, requires the tree to be projective. Default: ``False``.
            multiroot (bool):
                If ``False``, requires the tree to contain only a single root. Default: ``True``.

        Returns:
            ``True`` if the arcs form an valid tree, ``False`` otherwise.

        Examples:
            >>> CoNLL.istree([3, 0, 0, 3], multiroot=True)
            True
            >>> CoNLL.istree([3, 0, 0, 3], proj=True)
            False
        """

        from src.utils.alg import tarjan
        if proj and not cls.isprojective(sequence):
            return False
        n_roots = sum(head == 0 for head in sequence)
        if n_roots == 0:
            return False
        if not multiroot and n_roots > 1:
            return False
        if any(i == head for i, head in enumerate(sequence, 1)):
            return False
        return next(tarjan(sequence), None) is None

    def load(self, data, proj=False, max_len=None, **kwargs):
        """
        Load data in CoNLL-X format.
        Also support for loading data from CoNLL-U file with comments and non-integer IDs.

        Args:
            data (list[list] or str):
                A list of instances or a filename.
            proj (bool):
                If ``True``, discard all non-projective sentences. Default: ``False``.
            max_len (int):
                Sentences exceeding the length will be discarded. Default: ``None``.

        Returns:
            A list of CoNLLSentence instances.
        """

        if isinstance(data, str):
            with open(data, 'r') as f:
                lines = [line.strip() for line in f]
        else:
            data = [data] if isinstance(data[0], str) else data
            lines = '\n'.join([self.toconll(i) for i in data]).split('\n')

        i, start, sentences = 0, 0, []
        for line in progress_bar(lines, leave=False):
            if not line:
                sentences.append(CoNLLSentence(self, lines[start:i]))
                start = i + 1
            i += 1
        if proj:
            sentences = [i for i in sentences if self.isprojective(list(map(int, i.arcs)))]
        if max_len is not None:
            sentences = [i for i in sentences if len(i) < max_len]

        return sentences


class CoNLLSentence(Sentence):
    """
    Sencence in CoNLL-X format.

    Args:
        transform (CoNLL):
            A CoNLL object.
        lines (list[str]):
            A list of strings composing a sentence in CoNLL-X format.
            Comments and non-integer IDs are permitted.

    Examples:
        >>> lines = ['# text = But I found the location wonderful and the neighbors very kind.',
                     '1\tBut\t_\t_\t_\t_\t_\t_\t_\t_',
                     '2\tI\t_\t_\t_\t_\t_\t_\t_\t_',
                     '3\tfound\t_\t_\t_\t_\t_\t_\t_\t_',
                     '4\tthe\t_\t_\t_\t_\t_\t_\t_\t_',
                     '5\tlocation\t_\t_\t_\t_\t_\t_\t_\t_',
                     '6\twonderful\t_\t_\t_\t_\t_\t_\t_\t_',
                     '7\tand\t_\t_\t_\t_\t_\t_\t_\t_',
                     '7.1\tfound\t_\t_\t_\t_\t_\t_\t_\t_',
                     '8\tthe\t_\t_\t_\t_\t_\t_\t_\t_',
                     '9\tneighbors\t_\t_\t_\t_\t_\t_\t_\t_',
                     '10\tvery\t_\t_\t_\t_\t_\t_\t_\t_',
                     '11\tkind\t_\t_\t_\t_\t_\t_\t_\t_',
                     '12\t.\t_\t_\t_\t_\t_\t_\t_\t_']
        >>> sentence = CoNLLSentence(transform, lines)  # fields in transform are built from ptb.
        >>> sentence.arcs = [3, 3, 0, 5, 6, 3, 6, 9, 11, 11, 6, 3]
        >>> sentence.rels = ['cc', 'nsubj', 'root', 'det', 'nsubj', 'xcomp',
                             'cc', 'det', 'dep', 'advmod', 'conj', 'punct']
        >>> sentence
        # text = But I found the location wonderful and the neighbors very kind.
        1       But     _       _       _       _       3       cc      _       _
        2       I       _       _       _       _       3       nsubj   _       _
        3       found   _       _       _       _       0       root    _       _
        4       the     _       _       _       _       5       det     _       _
        5       location        _       _       _       _       6       nsubj   _       _
        6       wonderful       _       _       _       _       3       xcomp   _       _
        7       and     _       _       _       _       6       cc      _       _
        7.1     found   _       _       _       _       _       _       _       _
        8       the     _       _       _       _       9       det     _       _
        9       neighbors       _       _       _       _       11      dep     _       _
        10      very    _       _       _       _       11      advmod  _       _
        11      kind    _       _       _       _       6       conj    _       _
        12      .       _       _       _       _       3       punct   _       _
    """

    def __init__(self, transform, lines):
        super().__init__(transform)

        self.values = []
        # record annotations for post-recovery
        self.annotations = dict()

        for i, line in enumerate(lines):
            value = line.split('\t')
            if value[0].startswith('#') or not value[0].isdigit():
                self.annotations[-i - 1] = line
            else:
                self.annotations[len(self.values)] = line
                self.values.append(value)
        self.values = list(zip(*self.values))

    def __repr__(self):
        # cover the raw lines
        merged = {**self.annotations,
                  **{i: '\t'.join(map(str, line))
                     for i, line in enumerate(zip(*self.values))}}
        return '\n'.join(merged.values()) + '\n'


class Tree(Transform):
    """
    The Tree object factorize a constituency tree into four fields, each associated with one or more Field objects.

    Attributes:
        WORD:
            Words in the sentence.
        POS:
            Part-of-speech tags, or underscores if not available.
        TREE:
            The raw constituency tree in :class:`nltk.tree.Tree` format.
        CHART:
            The factorized sequence of binarized tree traversed in pre-order.
    """

    root = ''
    fields = ['WORD', 'POS', 'TREE', 'CHART', 'PARSINGORDER']

    def __init__(self, WORD=None, POS=None, TREE=None, CHART=None, PARSINGORDER=None):
        super().__init__()

        self.WORD = WORD
        self.POS = POS
        self.TREE = TREE
        self.CHART = CHART
        self.PARSINGORDER = PARSINGORDER

    @property
    def src(self):
        return self.WORD, self.POS, self.TREE

    @property
    def tgt(self):
        return self.CHART, self.PARSINGORDER

    @classmethod
    def totree(cls, tokens, root=''):
        """
        Convert a list of tokens to a nltk.Tree.
        Missing fields are filled with underscores.

        Args:
            tokens (list[str] or list[tuple]):
                This can be either a list of words or word/pos pairs.
            root (str):
                The root label of the tree. Default: ''.

        Returns:
            a nltk.Tree object.

        Examples:
            >>> print(Tree.totree(['She', 'enjoys', 'playing', 'tennis', '.'], 'TOP'))
            (TOP (_ She) (_ enjoys) (_ playing) (_ tennis) (_ .))
        """

        if isinstance(tokens[0], str):
            tokens = [(token, '_') for token in tokens]
        tree = ' '.join([f"({pos} {word})" for word, pos in tokens])
        return nltk.Tree.fromstring(f"({root} {tree})")

    @classmethod
    def binarize(cls, tree):
        """
        Conduct binarization over the tree.

        First, the tree is transformed to satisfy Chomsky Normal Form (CNF).
        Here we call the member function `chomsky_normal_form` in nltk.Tree to conduct left-binarization.
        Second, all unary productions in the tree are collapsed.

        Args:
            tree (nltk.tree.Tree):
                the tree to be binarized.

        Returns:
            the binarized tree.

        Examples:
            >>> tree = nltk.Tree.fromstring('''
                                            (TOP
                                              (S
                                                (NP (_ She))
                                                (VP (_ enjoys) (S (VP (_ playing) (NP (_ tennis)))))
                                                (_ .)))
                                            ''')
            >>> print(Tree.binarize(tree))
            (TOP
              (S
                (S|<>
                  (NP (_ She))
                  (VP
                    (VP|<> (_ enjoys))
                    (S+VP (VP|<> (_ playing)) (NP (_ tennis)))))
                (S|<> (_ .))))
        """

        tree = tree.copy(True)
        nodes = [tree]
        while nodes:
            node = nodes.pop()
            if isinstance(node, nltk.Tree):
                nodes.extend([child for child in node])
                if len(node) > 1:
                    for i, child in enumerate(node):
                        if not isinstance(child[0], nltk.Tree):
                            node[i] = nltk.Tree(f"{node.label()}|<>", [child])
        tree.chomsky_normal_form('left', 0, 0)
        tree.collapse_unary()

        return tree

    @classmethod
    def parsingorder_dfs(cls, tree, delete_labels=None, equal_labels=None):
        def track(tree, i):
            label = tree.label()
            if delete_labels is not None and label in delete_labels:
                label = None
            if equal_labels is not None:
                label = equal_labels.get(label, label)
            if len(tree) == 1 and not isinstance(tree[0], Tree):
                return (i + 1 if label is not None else i), []
            j, spans = i, []
            parsing_order = (i,)
            for child in tree:
                j, s = track(child, j)
                parsing_order = parsing_order + (j,)
                spans += s
            if len(parsing_order) == 3:
                spans = [parsing_order] + spans
            return j, spans

        return track(tree, 0)[1]

    @classmethod
    def factorize(cls, tree, delete_labels=None, equal_labels=None):
        """
        Factorize the tree into a sequence.
        The tree is traversed in pre-order.

        Args:
            tree (nltk.tree.Tree):
                the tree to be factorized.
            delete_labels (set[str]):
                A set of labels to be ignored. This is used for evaluation.
                If it is a pre-terminal label, delete the word along with the brackets.
                If it is a non-terminal label, just delete the brackets (don't delete childrens).
                In `EVALB`_, the default set is:
                {'TOP', 'S1', '-NONE-', ',', ':', '``', "''", '.', '?', '!', ''}
                Default: ``None``.
            equal_labels (dict[str, str]):
                The key-val pairs in the dict are considered equivalent (non-directional). This is used for evaluation.
                The default dict defined in EVALB is: {'ADVP': 'PRT'}
                Default: ``None``.

        Returns:
            The sequence of factorized tree.

        Examples:
            >>> tree = nltk.Tree.fromstring('''
                                            (TOP
                                              (S
                                                (NP (_ She))
                                                (VP (_ enjoys) (S (VP (_ playing) (NP (_ tennis)))))
                                                (_ .)))
                                            ''')
            >>> Tree.factorize(tree)
            [(0, 5, 'TOP'), (0, 5, 'S'), (0, 1, 'NP'), (1, 4, 'VP'), (2, 4, 'S'), (2, 4, 'VP'), (3, 4, 'NP')]
            >>> Tree.factorize(tree, delete_labels={'TOP', 'S1', '-NONE-', ',', ':', '``', "''", '.', '?', '!', ''})
            [(0, 5, 'S'), (0, 1, 'NP'), (1, 4, 'VP'), (2, 4, 'S'), (2, 4, 'VP'), (3, 4, 'NP')]

        .. _EVALB:
            https://nlp.cs.nyu.edu/evalb/
        """

        def track(tree, i):
            label = tree.label()
            if delete_labels is not None and label in delete_labels:
                label = None
            if equal_labels is not None:
                label = equal_labels.get(label, label)
            if len(tree) == 1 and not isinstance(tree[0], nltk.Tree):
                return (i + 1 if label is not None else i), []
            j, spans = i, []
            for child in tree:
                j, s = track(child, j)
                spans += s
            if label is not None and j > i:
                spans = [(i, j, label)] + spans
            return j, spans

        return track(tree, 0)[1]

    @classmethod
    def build(cls, tree, sequence):
        """
        Build a constituency tree from the sequence. The sequence is generated in pre-order.
        During building the tree, the sequence is de-binarized to the original format (i.e.,
        the suffixes ``|<>`` are ignored, the collapsed labels are recovered).

        Args:
            tree (nltk.tree.Tree):
                An empty tree providing a base for building a result tree.
            sequence (list[tuple]):
                A list of tuples used for generating a tree.
                Each tuple consits of the indices of left/right span boundaries and label of the span.

        Returns:
            A result constituency tree.

        Examples:
            >>> tree = Tree.totree(['She', 'enjoys', 'playing', 'tennis', '.'], 'TOP')
            >>> sequence = [(0, 5, 'S'), (0, 4, 'S|<>'), (0, 1, 'NP'), (1, 4, 'VP'), (1, 2, 'VP|<>'),
                            (2, 4, 'S+VP'), (2, 3, 'VP|<>'), (3, 4, 'NP'), (4, 5, 'S|<>')]
            >>> print(Tree.build(tree, sequence))
            (TOP
              (S
                (NP (_ She))
                (VP (_ enjoys) (S (VP (_ playing) (NP (_ tennis)))))
                (_ .)))
        """

        root = tree.label()
        leaves = [subtree for subtree in tree.subtrees()
                  if not isinstance(subtree[0], nltk.Tree)]

        def track(node):
            i, j, label = next(node)
            if j == i + 1:
                children = [leaves[i]]
            else:
                children = track(node) + track(node)
            if label.endswith('|<>'):
                return children
            labels = label.split('+')
            tree = nltk.Tree(labels[-1], children)
            for label in reversed(labels[:-1]):
                tree = nltk.Tree(label, [tree])
            return [tree]

        return nltk.Tree(root, track(iter(sequence)))

    def load(self, data, max_len=None, **kwargs):
        """
        Args:
            data (list[list] or str):
                A list of instances or a filename.
            max_len (int):
                Sentences exceeding the length will be discarded. Default: ``None``.

        Returns:
            A list of TreeSentence instances.
        """
        if isinstance(data, str):
            with open(data, 'r') as f:
                trees = [nltk.Tree.fromstring(string) for string in f]
            self.root = trees[0].label()
        else:
            data = [data] if isinstance(data[0], str) else data
            trees = [self.totree(i, self.root) for i in data]

        i, sentences = 0, []
        for tree in progress_bar(trees, leave=False):
            if len(tree) == 1 and not isinstance(tree[0][0], nltk.Tree):
                continue
            sentences.append(TreeSentence(self, tree))
            i += 1
        if max_len is not None:
            sentences = [i for i in sentences if len(i) < max_len]

        return sentences


class TreeSentence(Sentence):
    """
    Args:
        transform (Tree):
            A Tree object.
        tree (nltk.tree.Tree):
            A nltk.Tree object.
    """

    def __init__(self, transform, tree):
        super().__init__(transform)

        # the values contain words, pos tags, raw trees, and spans
        # the tree is first left-binarized before factorized
        # spans are the factorization of tree traversed in pre-order
        self.values = [*zip(*tree.pos()),
                       tree,
                       Tree.factorize(Tree.binarize(tree)[0]),
                       Tree.parsingorder_dfs(Tree.binarize(tree)[0])]

    def __repr__(self):
        return self.values[-3].pformat(1000000)


class DiscourseTree(Transform):
    """
    The Tree object factorize a constituency tree into four fields, each associated with one or more Field objects.

    Attributes:
        WORD:
            Words in the sentence.
        POS:
            Part-of-speech tags, or underscores if not available.
        TREE:
            The raw constituency tree in :class:`nltk.tree.Tree` format.
        CHART:
            The factorized sequence of binarized tree traversed in pre-order.
    """

    root = ''
    fields = ['WORD', 'EDU_BREAK', 'GOLD_METRIC', 'CHART', 'PARSINGORDER']

    def __init__(self, WORD=None, EDU_BREAK=None, GOLD_METRIC=None, CHART=None, PARSINGORDER=None):
        super().__init__()

        self.WORD = WORD
        self.EDU_BREAK = EDU_BREAK
        self.GOLD_METRIC = GOLD_METRIC
        self.CHART = CHART
        self.PARSINGORDER = PARSINGORDER

    @property
    def src(self):
        return self.WORD, self.EDU_BREAK, self.GOLD_METRIC

    @property
    def tgt(self):
        return self.CHART, self.PARSINGORDER

    @classmethod
    def edu2token(cls, golden_metric_edu, edu_break):
        # stack = [(0,len(parsing_order))]
        # NOTE
        # This part to generate parsing order and parsing label in term of edu
        # if golden_metric_edu is not 'NONE':
        if not (golden_metric_edu == 'NONE'):
            parsing_order_edu = []
            parsing_label = []
            golden_metric_edu_split = re.split(' ', golden_metric_edu)
            for each_split in golden_metric_edu_split:
                left_start, Nuclearity_left, Relation_left, left_end, \
                right_start, Nuclearity_right, Relation_right, right_end = re.split(':|=|,', each_split[1:-1])
                left_start = int(left_start) - 1
                left_end = int(left_end) - 1
                right_start = int(right_start) - 1
                right_end = int(right_end) - 1
                relation_label = RelationAndNucleus2Label(Nuclearity_left,
                                                          Nuclearity_right,
                                                          Relation_left,
                                                          Relation_right)
                parsing_order_edu.append((left_start, left_end, right_start, right_end))
                parsing_label.append(relation_label)

            # Now we add to the parsing edu the part that corresponding to edu detection component
            # (or when the case all the values)
            # in parsing order for edu are the same
            parsing_order_self_pointing_edu = []
            stacks = ['__StackRoot__', parsing_order_edu[0]]
            while stacks[-1] is not '__StackRoot__':
                stack_head = stacks[-1]
                assert (len(stack_head) == 4)
                parsing_order_self_pointing_edu.append(stack_head)
                if stack_head[0] == stack_head[1] and stack_head[2] == stack_head[3] and stack_head[2] == stack_head[1]:
                    del stacks[-1]
                elif stack_head[0] == stack_head[1] and stack_head[2] == stack_head[3]:
                    stack_top = (stack_head[0], stack_head[0], stack_head[0], stack_head[0])
                    stack_down = (stack_head[2], stack_head[2], stack_head[2], stack_head[2])
                    del stacks[-1]
                    stacks.append(stack_down)
                    stacks.append(stack_top)
                elif stack_head[0] == stack_head[1]:
                    stack_top = (stack_head[0], stack_head[0], stack_head[0], stack_head[0])
                    stack_down = [x for x in parsing_order_edu if x[0] == stack_head[2] and x[3] == stack_head[3]]
                    assert len(stack_down) == 1
                    stack_down = stack_down[0]
                    del stacks[-1]
                    stacks.append(stack_down)
                    stacks.append(stack_top)
                elif stack_head[2] == stack_head[3]:
                    stack_top = [x for x in parsing_order_edu if x[0] == stack_head[0] and x[3] == stack_head[1]]
                    stack_down = (stack_head[2], stack_head[2], stack_head[2], stack_head[2])
                    assert len(stack_top) == 1
                    stack_top = stack_top[0]
                    del stacks[-1]
                    stacks.append(stack_down)
                    stacks.append(stack_top)
                else:
                    stack_top = [x for x in parsing_order_edu if x[0] == stack_head[0] and x[3] == stack_head[1]]
                    stack_down = [x for x in parsing_order_edu if x[0] == stack_head[2] and x[3] == stack_head[3]]
                    assert len(stack_top) == 1 and len(stack_down) == 1
                    stack_top = stack_top[0]
                    stack_down = stack_down[0]
                    del stacks[-1]
                    stacks.append(stack_down)
                    stacks.append(stack_top)
            # parsing_label_self_pointing = []
            # for x in parsing_order_self_pointing_edu:
            #     if x in parsing_order_edu:
            #         parsing_label_self_pointing.append(parsing_label[parsing_order_edu.index(x)])
            #     else:
            #         parsing_label_self_pointing.append('None')
            edu_span = []
            for i in range(len(edu_break)):
                if i == 0:
                    edu_span.append((0, edu_break[0]))
                elif i < len(edu_break):
                    edu_span.append((edu_break[i - 1] + 1, edu_break[i]))

            parsing_order_self_pointing_token = []
            for x in parsing_order_self_pointing_edu:
                if x[0] == x[1] == x[2] == x[3]:
                    start_point = edu_span[x[0]][0]
                    end_point = edu_span[x[0]][1] + 1
                    parsing_order_self_pointing_token.append((start_point, end_point, end_point))
                else:
                    start_point = edu_span[x[0]][0]
                    split_point = edu_span[x[2]][0]
                    end_point = edu_span[x[3]][1] + 1
                    parsing_order_self_pointing_token.append((start_point, split_point, end_point))
            parsing_order_token = []
            for i, x in enumerate(parsing_order_edu):
                start_point = edu_span[x[0]][0]
                split_point = edu_span[x[2]][0]
                end_point = edu_span[x[3]][1] + 1
                parsing_order_token.append((start_point, split_point, end_point, parsing_label[i]))
            # parsing_order_token = []
            # for x in parsing_order_edu:
            #     start_leftspan = edu_span[x[0]][0]
            #     end_leftspan = edu_span[x[1]][1]
            #     start_rightspan = edu_span[x[2]][0]
            #     end_rightspan = edu_span[x[3]][1]
            #     parsing_order_token.append((start_leftspan, end_leftspan, start_rightspan, end_rightspan))
        else:
            # parsing_order_self_pointing_edu = [(0, 0, 0, 0)]
            # edu_span = [(0, edu_break[0])]
            # parsing_order_edu = []
            parsing_order_self_pointing_token = [(0, edu_break[0] + 1, edu_break[0] + 1)]
            parsing_order_token = []
            # parsing_label_self_pointing = ['None']
            # parsing_label = ['None']
        return parsing_order_token, parsing_order_self_pointing_token

    @classmethod
    def build(cls, sequence):
        """
        Build a token-based discourse tree from the sequence.
        The sequence is generated in depth first search.


        Args:
            sequence (list[tuple]):
                A list of tuples used for generating a tree.
                Each tuple consits of the indices of left/right/split span boundaries,
                discourse label of the span.

        Returns:
            A result discourse tree.

        Examples:
            >>> sequence = [(0, 24, 30, 'Attribution_NS'), (0, 12, 24,'Joint_NN'), (0, 12, 12,'None'),
                            (12, 16, 24,'Attribution_SN'), (12, 16, 16,'None'),
                            (16, 24, 24,'None'), (24, 30, 30,'None')]
            >>> print(Tree.build(sequence))
            '(0:Nucleus=span:23,24:Satellite=Attribution:29)
            (0:Nucleus=Joint:11,12:Nucleus=Joint:23)
            (12:Satellite=Attribution:15,16:Nucleus=span:23)'
        """
        if len(sequence) == 0:
            return 'NONE'
        else:
            result = []
            for (i, k, j, label) in sequence:
                if k < j:
                    Nuclearity_left, Nuclearity_right, Relation_left, Relation_right \
                        = Label2RelationAndNucleus(label)
                    node = f'({i}:{Nuclearity_left}={Relation_left}:{k - 1},{k}:{Nuclearity_right}={Relation_right}:{j - 1})'
                result.append(node)
            return ' '.join(result)

    @classmethod
    def build_gold(cls, edu_break, gold_metric_edu):
        """
		Build a token-based discourse tree from gold edu_break and gold_metric_edu.


		Args:
		    edu_break (list(int)):
		        A list of edu breaking points
			gold_metric_edu (string)
				edu-based discourse treel

		Returns:
			A result discourse tree.

		Examples:
			>>> edu_break =[11, 15, 23, 29, 32, 40]
			>>> gold_metric_edu='(1:Nucleus=span:3,4:Satellite=Attribution:4)
			                    (1:Nucleus=Joint:1,2:Nucleus=Joint:3)
			                    (2:Satellite=Attribution:2,3:Nucleus=span:3)'
			>>> print(Tree.build_gold(edu_break, gold_metric_edu))
			'(0:Nucleus=span:23,24:Satellite=Attribution:29)
			(0:Nucleus=Joint:11,12:Nucleus=Joint:23)
			(12:Satellite=Attribution:15,16:Nucleus=span:23)'
		"""
        if gold_metric_edu == 'NONE':
            return 'NONE'
        else:
            edu_span = []
            for i in range(len(edu_break)):
                if i == 0:
                    edu_span.append((0, edu_break[0]))
                elif i < len(edu_break):
                    edu_span.append((edu_break[i - 1] + 1, edu_break[i]))
            result = []
            golden_metric_edu_split = re.split(' ', gold_metric_edu)
            for each_split in golden_metric_edu_split:
                left_start, Nuclearity_left, Relation_left, left_end, \
                right_start, Nuclearity_right, Relation_right, right_end = re.split(':|=|,', each_split[1:-1])
                left_start = int(left_start) - 1
                left_end = int(left_end) - 1
                right_start = int(right_start) - 1
                right_end = int(right_end) - 1
                node = f'({edu_span[left_start][0]}:{Nuclearity_left}={Relation_left}:{edu_span[left_end][1]},{edu_span[right_start][0]}:{Nuclearity_right}={Relation_right}:{edu_span[right_end][1]})'
                result.append(node)
            return ' '.join(result)

    def load(self, data, max_len=None, **kwargs):
        """
        Args:
            data (dict):
                A dictionary of 'sentence', 'gold_metric', 'edu_break'
            max_len (int):
                Sentences exceeding the length will be discarded. Default: ``None``.

        Returns:
            A list of TreeSentence instances.
        """
        # if isinstance(data, str):
        #     with open(data, 'r') as f:
        #         trees = [nltk.Tree.fromstring(string) for string in f]
        #     self.root = trees[0].label()
        # else:
        #     data = [data] if isinstance(data[0], str) else data
        #     trees = [self.totree(i, self.root) for i in data]
        assert isinstance(data, str)
        import pickle
        data_dict = pickle.load(open(data, "rb"))
        sents = data_dict['InputDocs']
        edu_break = data_dict['EduBreak_TokenLevel']
        golden_metric = data_dict['Docs_structure']

        i, sentences = 0, []
        for sent in progress_bar(sents, leave=False):
            # if len(tree) == 1 and not isinstance(tree[0][0], nltk.Tree):
            #     continue
            sentences.append(DiscourseTreeSentence(self, sent, edu_break[i], golden_metric[i][0]))
            i += 1
        if max_len is not None:
            sentences = [i for i in sentences if len(i) < max_len]

        return sentences


class DiscourseTreeSentence(Sentence):
    """
    Args:
        transform (Tree):
            A Tree object.
        tree (nltk.tree.Tree):
            A nltk.Tree object.
    """

    def __init__(self, transform, sent, edu_break, golden_metric):
        super().__init__(transform)

        # the values contain words, pos tags, raw trees, and spans
        # the tree is first left-binarized before factorized
        # spans are the factorization of tree traversed in pre-order
        self.values = [sent,
                       edu_break,
                       golden_metric,
                       *DiscourseTree.edu2token(golden_metric_edu=golden_metric, edu_break=edu_break)]

    def __repr__(self):
        return self.values[-2].pformat(1000000)


class SPMRL_Tree(Transform):
    """
    The Tree object factorize a constituency tree into four fields, each associated with one or more Field objects.

    Attributes:
        WORD:
            Words in the sentence.
        POS:
            Part-of-speech tags, or underscores if not available.
        TREE:
            The raw constituency tree in :class:`nltk.tree.Tree` format.
        CHART:
            The factorized sequence of binarized tree traversed in pre-order.
    """

    root = ''
    fields = ['WORD', 'POS', 'TREE', 'CHART', 'PARSINGORDER']

    def __init__(self, WORD=None, POS=None, TREE=None, CHART=None, PARSINGORDER=None):
        super().__init__()

        self.WORD = WORD
        self.POS = POS
        self.TREE = TREE
        self.CHART = CHART
        self.PARSINGORDER = PARSINGORDER

    @property
    def src(self):
        return self.WORD, self.POS, self.TREE

    @property
    def tgt(self):
        return self.CHART, self.PARSINGORDER

    @classmethod
    def totree(cls, tokens, root=''):
        """
        Convert a list of tokens to a nltk.Tree.
        Missing fields are filled with underscores.

        Args:
            tokens (list[str] or list[tuple]):
                This can be either a list of words or word/pos pairs.
            root (str):
                The root label of the tree. Default: ''.

        Returns:
            a nltk.Tree object.

        Examples:
            >>> print(Tree.totree(['She', 'enjoys', 'playing', 'tennis', '.'], 'TOP'))
            (TOP (_ She) (_ enjoys) (_ playing) (_ tennis) (_ .))
        """

        if isinstance(tokens[0], str):
            tokens = [(token, '_') for token in tokens]
        tree = ' '.join([f"({pos} {word})" for word, pos in tokens])
        return nltk.Tree.fromstring(f"({root} {tree})")

    @classmethod
    def binarize(cls, tree, binarize_direction='left', dummy_label_manipulating='parent'):
        assert binarize_direction in ['left',
                                      'right'], f"We only support left/right direction here, yours: {binarize_direction}"
        assert dummy_label_manipulating in ['parent', 'universal',
                                            'universal_node_unary'], f"We only support parent/universal direction here"
        tree = tree.copy(True)
        nodes = [tree]
        while nodes:
            node = nodes.pop()
            if isinstance(node, nltk.Tree):
                nodes.extend([child for child in node])
                if len(node) > 1:
                    for i, child in enumerate(node):
                        if not isinstance(child[0], nltk.Tree):
                            if dummy_label_manipulating == 'parent':
                                node[i] = nltk.Tree(f"{node.label()}|<>", [child])
                            elif dummy_label_manipulating == 'universal':
                                node[i] = nltk.Tree(f"|<>", [child])
                            elif dummy_label_manipulating == 'universal_node_unary':
                                node[i] = nltk.Tree(f"UNARY|<>", [child])
        tree = custom_chomsky_normal_form(tree, binarize_direction, dummy_label_manipulating, 0, 0)
        tree.collapse_unary(joinChar="====")
        return tree

    # def binarize(cls, tree,binarize_direction='left'):
    #     """
    #     Conduct binarization over the tree.
    #
    #     First, the tree is transformed to satisfy Chomsky Normal Form (CNF).
    #     Here we call the member function `chomsky_normal_form` in nltk.Tree to conduct left-binarization.
    #     Second, all unary productions in the tree are collapsed.
    #
    #     Args:
    #         tree (nltk.tree.Tree):
    #             the tree to be binarized.
    #
    #     Returns:
    #         the binarized tree.
    #
    #     Examples:
    #         >>> tree = nltk.Tree.fromstring('''
    #                                         (TOP
    #                                           (S
    #                                             (NP (_ She))
    #                                             (VP (_ enjoys) (S (VP (_ playing) (NP (_ tennis)))))
    #                                             (_ .)))
    #                                         ''')
    #         >>> print(Tree.binarize(tree))
    #         (TOP
    #           (S
    #             (S|<>
    #               (NP (_ She))
    #               (VP
    #                 (VP|<> (_ enjoys))
    #                 (S+VP (VP|<> (_ playing)) (NP (_ tennis)))))
    #             (S|<> (_ .))))
    #     """
    #
    #     tree = tree.copy(True)
    #     nodes = [tree]
    #     while nodes:
    #         node = nodes.pop()
    #         if isinstance(node, nltk.Tree):
    #             nodes.extend([child for child in node])
    #             if len(node) > 1:
    #                 for i, child in enumerate(node):
    #                     if not isinstance(child[0], nltk.Tree):
    #                         node[i] = nltk.Tree(f"{node.label()}|<>", [child])
    #     tree.chomsky_normal_form(binarize_direction, 0, 0)
    #     tree.collapse_unary()
    #
    #     return tree

    @classmethod
    def parsingorder_dfs(cls, tree, delete_labels=None, equal_labels=None):
        def track(tree, i):
            label = tree.label()
            if delete_labels is not None and label in delete_labels:
                label = None
            if equal_labels is not None:
                label = equal_labels.get(label, label)
            if len(tree) == 1 and not isinstance(tree[0], Tree):
                return (i + 1 if label is not None else i), []
            j, spans = i, []
            parsing_order = (i,)
            for child in tree:
                j, s = track(child, j)
                parsing_order = parsing_order + (j,)
                spans += s
            if len(parsing_order) == 3:
                spans = [parsing_order] + spans
            return j, spans

        return track(tree, 0)[1]

    @classmethod
    def factorize(cls, tree, delete_labels=None, equal_labels=None):
        """
        Factorize the tree into a sequence.
        The tree is traversed in pre-order.

        Args:
            tree (nltk.tree.Tree):
                the tree to be factorized.
            delete_labels (set[str]):
                A set of labels to be ignored. This is used for evaluation.
                If it is a pre-terminal label, delete the word along with the brackets.
                If it is a non-terminal label, just delete the brackets (don't delete childrens).
                In `EVALB`_, the default set is:
                {'TOP', 'S1', '-NONE-', ',', ':', '``', "''", '.', '?', '!', ''}
                Default: ``None``.
            equal_labels (dict[str, str]):
                The key-val pairs in the dict are considered equivalent (non-directional). This is used for evaluation.
                The default dict defined in EVALB is: {'ADVP': 'PRT'}
                Default: ``None``.

        Returns:
            The sequence of factorized tree.

        Examples:
            >>> tree = nltk.Tree.fromstring('''
                                            (TOP
                                              (S
                                                (NP (_ She))
                                                (VP (_ enjoys) (S (VP (_ playing) (NP (_ tennis)))))
                                                (_ .)))
                                            ''')
            >>> Tree.factorize(tree)
            [(0, 5, 'TOP'), (0, 5, 'S'), (0, 1, 'NP'), (1, 4, 'VP'), (2, 4, 'S'), (2, 4, 'VP'), (3, 4, 'NP')]
            >>> Tree.factorize(tree, delete_labels={'TOP', 'S1', '-NONE-', ',', ':', '``', "''", '.', '?', '!', ''})
            [(0, 5, 'S'), (0, 1, 'NP'), (1, 4, 'VP'), (2, 4, 'S'), (2, 4, 'VP'), (3, 4, 'NP')]

        .. _EVALB:
            https://nlp.cs.nyu.edu/evalb/
        """

        def track(tree, i):
            label = tree.label()
            if delete_labels is not None and label in delete_labels:
                label = None
            if equal_labels is not None:
                label = equal_labels.get(label, label)
            if len(tree) == 1 and not isinstance(tree[0], nltk.Tree):
                return (i + 1 if label is not None else i), []
            j, spans = i, []
            for child in tree:
                j, s = track(child, j)
                spans += s
            if label is not None and j > i:
                spans = [(i, j, label)] + spans
            return j, spans

        return track(tree, 0)[1]

    @classmethod
    def build(cls, tree, sequence):
        """
        Build a constituency tree from the sequence. The sequence is generated in pre-order.
        During building the tree, the sequence is de-binarized to the original format (i.e.,
        the suffixes ``|<>`` are ignored, the collapsed labels are recovered).

        Args:
            tree (nltk.tree.Tree):
                An empty tree providing a base for building a result tree.
            sequence (list[tuple]):
                A list of tuples used for generating a tree.
                Each tuple consits of the indices of left/right span boundaries and label of the span.

        Returns:
            A result constituency tree.

        Examples:
            >>> tree = Tree.totree(['She', 'enjoys', 'playing', 'tennis', '.'], 'TOP')
            >>> sequence = [(0, 5, 'S'), (0, 4, 'S|<>'), (0, 1, 'NP'), (1, 4, 'VP'), (1, 2, 'VP|<>'),
                            (2, 4, 'S+VP'), (2, 3, 'VP|<>'), (3, 4, 'NP'), (4, 5, 'S|<>')]
            >>> print(Tree.build(tree, sequence))
            (TOP
              (S
                (NP (_ She))
                (VP (_ enjoys) (S (VP (_ playing) (NP (_ tennis)))))
                (_ .)))
        """

        root = tree.label()
        leaves = [subtree for subtree in tree.subtrees()
                  if not isinstance(subtree[0], nltk.Tree)]

        def track(node):
            i, j, label = next(node)
            if j == i + 1:
                children = [leaves[i]]
            else:
                children = track(node) + track(node)
            if label.endswith('|<>'):
                return children
            labels = label.split('====')
            tree = nltk.Tree(labels[-1], children)
            for label in reversed(labels[:-1]):
                tree = nltk.Tree(label, [tree])
            return [tree]

        return nltk.Tree(root, track(iter(sequence)))

    def load(self, data, max_len=None, binarize_direction='', dummy_label_manipulating='', **kwargs):
        """
        Args:
            data (list[list] or str):
                A list of instances or a filename.
            max_len (int):
                Sentences exceeding the length will be discarded. Default: ``None``.

        Returns:
            A list of TreeSentence instances.
        """

        if isinstance(data, str):
            with open(data, 'r') as f:
                trees = [nltk.Tree.fromstring(string) for string in f]
            self.root = trees[0].label()
        else:
            data = [data] if isinstance(data[0], str) else data
            trees = [self.totree(i, self.root) for i in data]

        i, sentences = 0, []
        for tree in progress_bar(trees, leave=False):
            if len(tree) == 1 and not isinstance(tree[0][0], nltk.Tree):
                continue
            # sentences.append(SPMRL_TreeSentence(self, tree, binarize_direction,dummy_label_manipulating))
            sentences.append(SPMRL_TreeSentence(self, tree, binarize_direction, dummy_label_manipulating))
            i += 1
        if max_len is not None:
            sentences = [i for i in sentences if len(i) < max_len]

        return sentences


class SPMRL_TreeSentence(Sentence):
    """
    Args:
        transform (Tree):
            A Tree object.
        tree (nltk.tree.Tree):
            A nltk.Tree object.
    """

    def __init__(self, transform, tree, binarize_direction, dummy_label_manipulating):
        super().__init__(transform)

        # the values contain words, pos tags, raw trees, and spans
        # the tree is first left-binarized before factorized
        # spans are the factorization of tree traversed in pre-order
        if len(tree) > 1:
            self.values = [*zip(*tree.pos()),
                           tree,
                           SPMRL_Tree.factorize(SPMRL_Tree.binarize(tree, binarize_direction=binarize_direction,
                                                                    dummy_label_manipulating=dummy_label_manipulating)),
                           SPMRL_Tree.parsingorder_dfs(SPMRL_Tree.binarize(tree, binarize_direction=binarize_direction,
                                                                           dummy_label_manipulating=dummy_label_manipulating))]
        else:
            self.values = [*zip(*tree.pos()),
                           tree,
                           SPMRL_Tree.factorize(SPMRL_Tree.binarize(tree, binarize_direction=binarize_direction,
                                                                    dummy_label_manipulating=dummy_label_manipulating)[
                                                    0]),
                           SPMRL_Tree.parsingorder_dfs(SPMRL_Tree.binarize(tree, binarize_direction=binarize_direction,
                                                                           dummy_label_manipulating=dummy_label_manipulating)[
                                                           0])]

    def __repr__(self):
        return self.values[-3].pformat(1000000)


class TreeZh(Transform):
    """
    The Tree object factorize a constituency tree into four fields, each associated with one or more Field objects.

    Attributes:
        WORD:
            Words in the sentence.
        POS:
            Part-of-speech tags, or underscores if not available.
        TREE:
            The raw constituency tree in :class:`nltk.tree.Tree` format.
        CHART:
            The factorized sequence of binarized tree traversed in pre-order.
    """

    root = ''
    fields = ['WORD', 'POS', 'TREE', 'CHART', 'PARSINGORDER']

    def __init__(self, WORD=None, POS=None, TREE=None, CHART=None, PARSINGORDER=None):
        super().__init__()

        self.WORD = WORD
        self.POS = POS
        self.TREE = TREE
        self.CHART = CHART
        self.PARSINGORDER = PARSINGORDER

    @property
    def src(self):
        return self.WORD, self.POS, self.TREE

    @property
    def tgt(self):
        return self.CHART, self.PARSINGORDER

    @classmethod
    def totree(cls, tokens, root=''):
        """
        Convert a list of tokens to a nltk.Tree.
        Missing fields are filled with underscores.

        Args:
            tokens (list[str] or list[tuple]):
                This can be either a list of words or word/pos pairs.
            root (str):
                The root label of the tree. Default: ''.

        Returns:
            a nltk.Tree object.

        Examples:
            >>> print(Tree.totree(['She', 'enjoys', 'playing', 'tennis', '.'], 'TOP'))
            (TOP (_ She) (_ enjoys) (_ playing) (_ tennis) (_ .))
        """

        if isinstance(tokens[0], str):
            tokens = [(token, '_') for token in tokens]
        tree = ' '.join([f"({pos} {word})" for word, pos in tokens])
        return nltk.Tree.fromstring(f"({root} {tree})")

    @classmethod
    def binarize(cls, tree):
        """
        Conduct binarization over the tree.

        First, the tree is transformed to satisfy Chomsky Normal Form (CNF).
        Here we call the member function `chomsky_normal_form` in nltk.Tree to conduct left-binarization.
        Second, all unary productions in the tree are collapsed.

        Args:
            tree (nltk.tree.Tree):
                the tree to be binarized.

        Returns:
            the binarized tree.

        Examples:
            >>> tree = nltk.Tree.fromstring('''
                                            (TOP
                                              (S
                                                (NP (_ She))
                                                (VP (_ enjoys) (S (VP (_ playing) (NP (_ tennis)))))
                                                (_ .)))
                                            ''')
            >>> print(Tree.binarize(tree))
            (TOP
              (S
                (S|<>
                  (NP (_ She))
                  (VP
                    (VP|<> (_ enjoys))
                    (S+VP (VP|<> (_ playing)) (NP (_ tennis)))))
                (S|<> (_ .))))
        """

        tree = tree.copy(True)
        nodes = [tree]
        while nodes:
            node = nodes.pop()
            if isinstance(node, nltk.Tree):
                nodes.extend([child for child in node])
                if len(node) > 1:
                    for i, child in enumerate(node):
                        if not isinstance(child[0], nltk.Tree):
                            node[i] = nltk.Tree(f"{node.label()}|<>", [child])
        tree.chomsky_normal_form('left', 0, 0)
        tree.collapse_unary()

        return tree

    @classmethod
    def parsingorder_dfs(cls, tree, delete_labels=None, equal_labels=None):
        def track(tree, i):
            label = tree.label()
            if delete_labels is not None and label in delete_labels:
                label = None
            if equal_labels is not None:
                label = equal_labels.get(label, label)
            if len(tree) == 1 and not isinstance(tree[0], Tree):
                return (i + 1 if label is not None else i), []
            j, spans = i, []
            parsing_order = (i,)
            for child in tree:
                j, s = track(child, j)
                parsing_order = parsing_order + (j,)
                spans += s
            if len(parsing_order) == 3:
                spans = [parsing_order] + spans
            return j, spans

        return track(tree, 0)[1]

    @classmethod
    def factorize(cls, tree, delete_labels=None, equal_labels=None):
        """
        Factorize the tree into a sequence.
        The tree is traversed in pre-order.

        Args:
            tree (nltk.tree.Tree):
                the tree to be factorized.
            delete_labels (set[str]):
                A set of labels to be ignored. This is used for evaluation.
                If it is a pre-terminal label, delete the word along with the brackets.
                If it is a non-terminal label, just delete the brackets (don't delete childrens).
                In `EVALB`_, the default set is:
                {'TOP', 'S1', '-NONE-', ',', ':', '``', "''", '.', '?', '!', ''}
                Default: ``None``.
            equal_labels (dict[str, str]):
                The key-val pairs in the dict are considered equivalent (non-directional). This is used for evaluation.
                The default dict defined in EVALB is: {'ADVP': 'PRT'}
                Default: ``None``.

        Returns:
            The sequence of factorized tree.

        Examples:
            >>> tree = nltk.Tree.fromstring('''
                                            (TOP
                                              (S
                                                (NP (_ She))
                                                (VP (_ enjoys) (S (VP (_ playing) (NP (_ tennis)))))
                                                (_ .)))
                                            ''')
            >>> Tree.factorize(tree)
            [(0, 5, 'TOP'), (0, 5, 'S'), (0, 1, 'NP'), (1, 4, 'VP'), (2, 4, 'S'), (2, 4, 'VP'), (3, 4, 'NP')]
            >>> Tree.factorize(tree, delete_labels={'TOP', 'S1', '-NONE-', ',', ':', '``', "''", '.', '?', '!', ''})
            [(0, 5, 'S'), (0, 1, 'NP'), (1, 4, 'VP'), (2, 4, 'S'), (2, 4, 'VP'), (3, 4, 'NP')]

        .. _EVALB:
            https://nlp.cs.nyu.edu/evalb/
        """

        def track(tree, i):
            label = tree.label()
            if delete_labels is not None and label in delete_labels:
                label = None
            if equal_labels is not None:
                label = equal_labels.get(label, label)
            if len(tree) == 1 and not isinstance(tree[0], nltk.Tree):
                return (i + 1 if label is not None else i), []
            j, spans = i, []
            for child in tree:
                j, s = track(child, j)
                spans += s
            if label is not None and j > i:
                spans = [(i, j, label)] + spans
            return j, spans

        return track(tree, 0)[1]

    @classmethod
    def build(cls, tree, sequence):
        """
        Build a constituency tree from the sequence. The sequence is generated in pre-order.
        During building the tree, the sequence is de-binarized to the original format (i.e.,
        the suffixes ``|<>`` are ignored, the collapsed labels are recovered).

        Args:
            tree (nltk.tree.Tree):
                An empty tree providing a base for building a result tree.
            sequence (list[tuple]):
                A list of tuples used for generating a tree.
                Each tuple consits of the indices of left/right span boundaries and label of the span.

        Returns:
            A result constituency tree.

        Examples:
            >>> tree = Tree.totree(['She', 'enjoys', 'playing', 'tennis', '.'], 'TOP')
            >>> sequence = [(0, 5, 'S'), (0, 4, 'S|<>'), (0, 1, 'NP'), (1, 4, 'VP'), (1, 2, 'VP|<>'),
                            (2, 4, 'S+VP'), (2, 3, 'VP|<>'), (3, 4, 'NP'), (4, 5, 'S|<>')]
            >>> print(Tree.build(tree, sequence))
            (TOP
              (S
                (NP (_ She))
                (VP (_ enjoys) (S (VP (_ playing) (NP (_ tennis)))))
                (_ .)))
        """

        root = tree.label()
        leaves = [subtree for subtree in tree.subtrees()
                  if not isinstance(subtree[0], nltk.Tree)]

        def track(node):
            i, j, label = next(node)
            if j == i + 1:
                children = [leaves[i]]
            else:
                children = track(node) + track(node)
            if label.endswith('|<>'):
                return children
            labels = label.split('+')
            tree = nltk.Tree(labels[-1], children)
            for label in reversed(labels[:-1]):
                tree = nltk.Tree(label, [tree])
            return [tree]

        return nltk.Tree(root, track(iter(sequence)))

    def load(self, data, max_len=None, **kwargs):
        """
        Args:
            data (list[list] or str):
                A list of instances or a filename.
            max_len (int):
                Sentences exceeding the length will be discarded. Default: ``None``.

        Returns:
            A list of TreeSentence instances.
        """
        if isinstance(data, str):
            with open(data, 'r') as f:
                trees = [nltk.Tree.fromstring(string) for string in f]
            self.root = trees[0].label()
        else:
            data = [data] if isinstance(data[0], str) else data
            trees = [self.totree(i, self.root) for i in data]

        i, sentences = 0, []
        for tree in progress_bar(trees, leave=False):
            if len(tree) == 1 and not isinstance(tree[0][0], nltk.Tree):
                continue
            sentences.append(TreeZhSentence(self, tree))
            i += 1
        if max_len is not None:
            sentences = [i for i in sentences if len(i) < max_len]

        return sentences


class TreeZhSentence(Sentence):
    """
    Args:
        transform (Tree):
            A Tree object.
        tree (nltk.tree.Tree):
            A nltk.Tree object.
    """

    def __init__(self, transform, tree):
        super().__init__(transform)

        # the values contain words, pos tags, raw trees, and spans
        # the tree is first left-binarized before factorized
        # spans are the factorization of tree traversed in pre-order
        self.values = [*zip(*tree.pos()),
                       tree,
                       Tree.factorize(TreeZh.binarize(tree)),
                       Tree.parsingorder_dfs(TreeZh.binarize(tree))]

    def __repr__(self):
        return self.values[-3].pformat(1000000)


def collect_edus(docs_structure):
    edus_id = []
    for entry in docs_structure:
        if entry:
            left, right = entry.split(',')
            left = left.replace('(', '').split(':')
            du1, du2 = left[0], left[2]
            if du1 == du2:
                edus_id.append(int(du1))

            right = right.replace(')', '').split(':')
            du1, du2 = right[0], right[2]
            if du1 == du2:
                edus_id.append(int(du1))
    return edus_id


class DiscourseTreeDoc(Transform):
    """
    The Tree object factorize a constituency tree into four fields, each associated with one or more Field objects.

    Attributes:
        WORD:
            Words in the sentence.
        POS:
            Part-of-speech tags, or underscores if not available.
        TREE:
            The raw constituency tree in :class:`nltk.tree.Tree` format.
        CHART:
            The factorized sequence of binarized tree traversed in pre-order.
    """

    root = ''
    fields = ['WORD', 'EDU_BREAK', 'GOLD_METRIC', 'CHART', 'PARSINGORDER']

    def __init__(self, WORD=None, EDU_BREAK=None, GOLD_METRIC=None, CHART=None, PARSINGORDER=None):
        super().__init__()

        self.WORD = WORD
        self.EDU_BREAK = EDU_BREAK
        self.GOLD_METRIC = GOLD_METRIC
        self.CHART = CHART
        self.PARSINGORDER = PARSINGORDER

    @property
    def src(self):
        return self.WORD, self.EDU_BREAK, self.GOLD_METRIC

    @property
    def tgt(self):
        return self.CHART, self.PARSINGORDER

    @classmethod
    def edu2token(cls, golden_metric_edu, edu_break):
        # stack = [(0,len(parsing_order))]
        # NOTE
        # This part to generate parsing order and parsing label in term of edu
        # if golden_metric_edu is not 'NONE':
        # print(edu_break)
        # print(len(edu_break))
        # input()
        # print(golden_metric_edu)
        # print(edu_break)
        # input()
        if not (golden_metric_edu == 'NONE'):
            parsing_order_edu = []
            parsing_label = []
            golden_metric_edu_split = re.split(' ', golden_metric_edu)
            #             assert (len(golden_metric_edu_split) == len(edu_break) - 1), f"something wrong {len(golden_metric_edu_split)}:{len(edu_break) - 1}"
            rootsplit = None

            edus_numbers = collect_edus(golden_metric_edu_split)
            if edus_numbers:
                maximum_edu = max(edus_numbers)
                for each_split in golden_metric_edu_split:
                    if each_split:
                        left_start, Nuclearity_left, Relation_left, left_end, \
                        right_start, Nuclearity_right, Relation_right, right_end = re.split('[:|=|,]', each_split[1:-1])
                        left_start = int(left_start) - 1
                        left_end = int(left_end) - 1
                        right_start = int(right_start) - 1
                        right_end = int(right_end) - 1
                        relation_label = RelationAndNucleus2Label(Nuclearity_left,
                                                                  Nuclearity_right,
                                                                  Relation_left,
                                                                  Relation_right)
                        parsing_order_edu.append((left_start, left_end, right_start, right_end))
                        if left_start == 0 and right_end == maximum_edu - 1:
                            rootsplit = deepcopy((left_start, left_end, right_start, right_end))
                        parsing_label.append(relation_label)

            # Now we add to the parsing edu the part that corresponding to edu detection component
            # (or when the case all the values)
            # in parsing order for edu are the same
            parsing_order_self_pointing_edu = []
            stacks = ['__StackRoot__', rootsplit]
            while stacks[-1] is not '__StackRoot__':
                stack_head = stacks[-1]
                assert (len(stack_head) == 4)
                parsing_order_self_pointing_edu.append(stack_head)
                if stack_head[0] == stack_head[1] and stack_head[2] == stack_head[3] and stack_head[2] == stack_head[1]:
                    del stacks[-1]
                elif stack_head[0] == stack_head[1] and stack_head[2] == stack_head[3]:
                    stack_top = (stack_head[0], stack_head[0], stack_head[0], stack_head[0])
                    stack_down = (stack_head[2], stack_head[2], stack_head[2], stack_head[2])
                    del stacks[-1]
                    stacks.append(stack_down)
                    stacks.append(stack_top)
                elif stack_head[0] == stack_head[1]:
                    stack_top = (stack_head[0], stack_head[0], stack_head[0], stack_head[0])
                    stack_down = [x for x in parsing_order_edu if x[0] == stack_head[2] and x[3] == stack_head[3]]
                    assert len(
                        stack_down) == 1, f"something wrong, \n{golden_metric_edu}, \n{edu_break}, \n{stack_down}"
                    stack_down = stack_down[0]
                    del stacks[-1]
                    stacks.append(stack_down)
                    stacks.append(stack_top)
                elif stack_head[2] == stack_head[3]:
                    stack_top = [x for x in parsing_order_edu if x[0] == stack_head[0] and x[3] == stack_head[1]]
                    stack_down = (stack_head[2], stack_head[2], stack_head[2], stack_head[2])
                    assert len(stack_top) == 1
                    stack_top = stack_top[0]
                    del stacks[-1]
                    stacks.append(stack_down)
                    stacks.append(stack_top)
                else:
                    stack_top = [x for x in parsing_order_edu if x[0] == stack_head[0] and x[3] == stack_head[1]]
                    stack_down = [x for x in parsing_order_edu if x[0] == stack_head[2] and x[3] == stack_head[3]]
                    assert len(stack_top) == 1 and len(stack_down) == 1
                    stack_top = stack_top[0]
                    stack_down = stack_down[0]
                    del stacks[-1]
                    stacks.append(stack_down)
                    stacks.append(stack_top)
            # parsing_label_self_pointing = []
            # for x in parsing_order_self_pointing_edu:
            #     if x in parsing_order_edu:
            #         parsing_label_self_pointing.append(parsing_label[parsing_order_edu.index(x)])
            #     else:
            #         parsing_label_self_pointing.append('None')
            edu_span = []
            for i in range(len(edu_break)):
                if i == 0:
                    edu_span.append((0, edu_break[0]))
                elif i < len(edu_break):
                    edu_span.append((edu_break[i - 1] + 1, edu_break[i]))

            parsing_order_self_pointing_token = []
            for x in parsing_order_self_pointing_edu:
                if x[0] == x[1] == x[2] == x[3]:
                    start_point = edu_span[x[0]][0]
                    end_point = edu_span[x[0]][1] + 1
                    parsing_order_self_pointing_token.append((start_point, end_point, end_point))
                else:
                    start_point = edu_span[x[0]][0]
                    split_point = edu_span[x[2]][0]
                    end_point = edu_span[x[3]][1] + 1
                    parsing_order_self_pointing_token.append((start_point, split_point, end_point))
            parsing_order_token = []
            # print(len(parsing_order_edu))
            # print(edu_span)
            # print(parsing_order_edu)
            for i, x in enumerate(parsing_order_edu):
                start_point = edu_span[x[0]][0]
                split_point = edu_span[x[2]][0]
                end_point = edu_span[x[3]][1] + 1
                parsing_order_token.append((start_point, split_point, end_point, parsing_label[i]))
            # parsing_order_token = []
            # for x in parsing_order_edu:
            #     start_leftspan = edu_span[x[0]][0]
            #     end_leftspan = edu_span[x[1]][1]
            #     start_rightspan = edu_span[x[2]][0]
            #     end_rightspan = edu_span[x[3]][1]
            #     parsing_order_token.append((start_leftspan, end_leftspan, start_rightspan, end_rightspan))
        else:
            # parsing_order_self_pointing_edu = [(0, 0, 0, 0)]
            # edu_span = [(0, edu_break[0])]
            # parsing_order_edu = []
            parsing_order_self_pointing_token = [(0, edu_break[0] + 1, edu_break[0] + 1)]
            parsing_order_token = []
            # parsing_label_self_pointing = ['None']
            # parsing_label = ['None']
        return parsing_order_token, parsing_order_self_pointing_token

    @classmethod
    def build(cls, sequence):
        """
        Build a token-based discourse tree from the sequence.
        The sequence is generated in depth first search.


        Args:
            sequence (list[tuple]):
                A list of tuples used for generating a tree.
                Each tuple consits of the indices of left/right/split span boundaries,
                discourse label of the span.

        Returns:
            A result discourse tree.

        Examples:
            >>> sequence = [(0, 24, 30, 'Attribution_NS'), (0, 12, 24,'Joint_NN'), (0, 12, 12,'None'),
                            (12, 16, 24,'Attribution_SN'), (12, 16, 16,'None'),
                            (16, 24, 24,'None'), (24, 30, 30,'None')]
            >>> print(Tree.build(sequence))
            '(0:Nucleus=span:23,24:Satellite=Attribution:29)
            (0:Nucleus=Joint:11,12:Nucleus=Joint:23)
            (12:Satellite=Attribution:15,16:Nucleus=span:23)'
        """
        if len(sequence) == 0:
            return 'NONE'
        else:
            result = []
            for (i, k, j, label) in sequence:
                if k < j:
                    Nuclearity_left, Nuclearity_right, Relation_left, Relation_right \
                        = Label2RelationAndNucleus(label)
                    node = f'({i}:{Nuclearity_left}={Relation_left}:{k - 1},{k}:{Nuclearity_right}={Relation_right}:{j - 1})'
                result.append(node)
            return ' '.join(result)

    @classmethod
    def build_gold(cls, edu_break, gold_metric_edu):
        """
		Build a token-based discourse tree from gold edu_break and gold_metric_edu.


		Args:
		    edu_break (list(int)):
		        A list of edu breaking points
			gold_metric_edu (string)
				edu-based discourse treel

		Returns:
			A result discourse tree.

		Examples:
			>>> edu_break =[11, 15, 23, 29, 32, 40]
			>>> gold_metric_edu='(1:Nucleus=span:3,4:Satellite=Attribution:4)
			                    (1:Nucleus=Joint:1,2:Nucleus=Joint:3)
			                    (2:Satellite=Attribution:2,3:Nucleus=span:3)'
			>>> print(Tree.build_gold(edu_break, gold_metric_edu))
			'(0:Nucleus=span:23,24:Satellite=Attribution:29)
			(0:Nucleus=Joint:11,12:Nucleus=Joint:23)
			(12:Satellite=Attribution:15,16:Nucleus=span:23)'
		"""
        if gold_metric_edu == 'NONE':
            return 'NONE'
        else:
            edu_span = []
            for i in range(len(edu_break)):
                if i == 0:
                    edu_span.append((0, edu_break[0]))
                elif i < len(edu_break):
                    edu_span.append((edu_break[i - 1] + 1, edu_break[i]))
            result = []
            golden_metric_edu_split = re.split(' ', gold_metric_edu)
            for each_split in golden_metric_edu_split:
                left_start, Nuclearity_left, Relation_left, left_end, \
                right_start, Nuclearity_right, Relation_right, right_end = re.split(':|=|,', each_split[1:-1])
                left_start = int(left_start) - 1
                left_end = int(left_end) - 1
                right_start = int(right_start) - 1
                right_end = int(right_end) - 1
                node = f'({edu_span[left_start][0]}:{Nuclearity_left}={Relation_left}:{edu_span[left_end][1]},{edu_span[right_start][0]}:{Nuclearity_right}={Relation_right}:{edu_span[right_end][1]})'
                result.append(node)
            return ' '.join(result)

    def load(self, data, max_len=None, **kwargs):
        """
        Args:
            data (dict):
                A dictionary of 'sentence', 'gold_metric', 'edu_break'
            max_len (int):
                Sentences exceeding the length will be discarded. Default: ``None``.

        Returns:
            A list of TreeSentence instances.
        """
        # if isinstance(data, str):
        #     with open(data, 'r') as f:
        #         trees = [nltk.Tree.fromstring(string) for string in f]
        #     self.root = trees[0].label()
        # else:
        #     data = [data] if isinstance(data[0], str) else data
        #     trees = [self.totree(i, self.root) for i in data]
        assert isinstance(data, str)
        import pickle
        data_dict = pickle.load(open(data, "rb"))
        sents = data_dict['InputDocs']
        edu_break = data_dict['EduBreak_TokenLevel']
        golden_metric = data_dict['Docs_structure']

        i, sentences = 0, []
        for sent in progress_bar(sents, leave=False):
            # if len(tree) == 1 and not isinstance(tree[0][0], nltk.Tree):
            #     continue
            sentences.append(DiscourseTreeDocToken(self, sent, edu_break[i], ' '.join(golden_metric[i])))
            i += 1
        if max_len is not None:
            sentences = [i for i in sentences if len(i) < max_len]

        return sentences


class DiscourseTreeDocToken(Sentence):
    """
    Args:
        transform (Tree):
            A Tree object.
        tree (nltk.tree.Tree):
            A nltk.Tree object.
    """

    def __init__(self, transform, sent, edu_break, golden_metric):
        super().__init__(transform)

        # the values contain words, pos tags, raw trees, and spans
        # the tree is first left-binarized before factorized
        # spans are the factorization of tree traversed in pre-order
        self.values = [sent,
                       edu_break,
                       golden_metric,
                       *DiscourseTreeDoc.edu2token(golden_metric_edu=golden_metric, edu_break=edu_break)]

    def __repr__(self):
        return self.values[-2].pformat(1000000)


class DiscourseTreeDocSentinfo(Transform):
    """
    The Tree object factorize a constituency tree into four fields, each associated with one or more Field objects.

    Attributes:
        WORD:
            Words in the sentence.
        POS:
            Part-of-speech tags, or underscores if not available.
        TREE:
            The raw constituency tree in :class:`nltk.tree.Tree` format.
        CHART:
            The factorized sequence of binarized tree traversed in pre-order.
    """

    root = ''
    fields = ['WORD', 'ORIGINAL_EDU_BREAK', 'GOLD_METRIC', 'SENT_BREAK', 'EDU_BREAK', 'CHART', 'GOLDPARSINGORDER',
              'PARSINGORDER']

    def __init__(self, WORD=None, ORIGINAL_EDU_BREAK=None, GOLD_METRIC=None,
                 SENT_BREAK=None, EDU_BREAK=None, CHART=None, GOLDPARSINGORDER=None, PARSINGORDER=None):
        super().__init__()

        self.WORD = WORD
        self.SENT_BREAK = SENT_BREAK
        self.EDU_BREAK = EDU_BREAK
        self.ORIGINAL_EDU_BREAK = ORIGINAL_EDU_BREAK
        self.GOLD_METRIC = GOLD_METRIC
        self.CHART = CHART
        self.GOLDPARSINGORDER = GOLDPARSINGORDER
        self.PARSINGORDER = PARSINGORDER

    @property
    def src(self):
        return self.WORD, self.GOLD_METRIC, self.SENT_BREAK, self.EDU_BREAK, self.ORIGINAL_EDU_BREAK

    @property
    def tgt(self):
        return self.CHART, self.PARSINGORDER, self.GOLDPARSINGORDER

    @classmethod
    def edu2token(cls, golden_metric_edu, edu_break, sent_break):
        # stack = [(0,len(parsing_order))]
        # NOTE
        # This part to generate parsing order and parsing label in term of edu
        # if golden_metric_edu is not 'NONE':
        # print(edu_break)
        # print(len(edu_break))
        # input()
        if not (golden_metric_edu == 'NONE'):
            parsing_order_edu = []
            parsing_label = []
            golden_metric_edu_split = re.split(' ', golden_metric_edu)
            assert (len(golden_metric_edu_split) == len(
                edu_break) - 1), f"something wrong {len(golden_metric_edu_split)}:{len(edu_break) - 1}"
            rootsplit = None
            for each_split in golden_metric_edu_split:
                left_start, Nuclearity_left, Relation_left, left_end, \
                right_start, Nuclearity_right, Relation_right, right_end = re.split(':|=|,', each_split[1:-1])
                left_start = int(left_start) - 1
                left_end = int(left_end) - 1
                right_start = int(right_start) - 1
                right_end = int(right_end) - 1
                relation_label = RelationAndNucleus2Label(Nuclearity_left,
                                                          Nuclearity_right,
                                                          Relation_left,
                                                          Relation_right)
                parsing_order_edu.append((left_start, left_end, right_start, right_end))
                if left_start == 0 and right_end == len(edu_break) - 1:
                    rootsplit = deepcopy((left_start, left_end, right_start, right_end))
                parsing_label.append(relation_label)

            # Now we add to the parsing edu the part that corresponding to edu detection component
            # (or when the case all the values)
            # in parsing order for edu are the same
            parsing_order_self_pointing_edu = []
            stacks = ['__StackRoot__', rootsplit]
            while stacks[-1] is not '__StackRoot__':
                stack_head = stacks[-1]
                assert (len(stack_head) == 4)
                parsing_order_self_pointing_edu.append(stack_head)
                if stack_head[0] == stack_head[1] and stack_head[2] == stack_head[3] and stack_head[2] == stack_head[1]:
                    del stacks[-1]
                elif stack_head[0] == stack_head[1] and stack_head[2] == stack_head[3]:
                    stack_top = (stack_head[0], stack_head[0], stack_head[0], stack_head[0])
                    stack_down = (stack_head[2], stack_head[2], stack_head[2], stack_head[2])
                    del stacks[-1]
                    stacks.append(stack_down)
                    stacks.append(stack_top)
                elif stack_head[0] == stack_head[1]:
                    stack_top = (stack_head[0], stack_head[0], stack_head[0], stack_head[0])
                    stack_down = [x for x in parsing_order_edu if x[0] == stack_head[2] and x[3] == stack_head[3]]
                    assert len(
                        stack_down) == 1, f"something wrong, \n{golden_metric_edu}, \n{edu_break}, \n{stack_down}"
                    stack_down = stack_down[0]
                    del stacks[-1]
                    stacks.append(stack_down)
                    stacks.append(stack_top)
                elif stack_head[2] == stack_head[3]:
                    stack_top = [x for x in parsing_order_edu if x[0] == stack_head[0] and x[3] == stack_head[1]]
                    stack_down = (stack_head[2], stack_head[2], stack_head[2], stack_head[2])
                    assert len(stack_top) == 1
                    stack_top = stack_top[0]
                    del stacks[-1]
                    stacks.append(stack_down)
                    stacks.append(stack_top)
                else:
                    stack_top = [x for x in parsing_order_edu if x[0] == stack_head[0] and x[3] == stack_head[1]]
                    stack_down = [x for x in parsing_order_edu if x[0] == stack_head[2] and x[3] == stack_head[3]]
                    assert len(stack_top) == 1 and len(stack_down) == 1
                    stack_top = stack_top[0]
                    stack_down = stack_down[0]
                    del stacks[-1]
                    stacks.append(stack_down)
                    stacks.append(stack_top)
            # parsing_label_self_pointing = []
            # for x in parsing_order_self_pointing_edu:
            #     if x in parsing_order_edu:
            #         parsing_label_self_pointing.append(parsing_label[parsing_order_edu.index(x)])
            #     else:
            #         parsing_label_self_pointing.append('None')
            edu_span = []
            for i in range(len(edu_break)):
                if i == 0:
                    edu_span.append((0, edu_break[0]))
                elif i < len(edu_break):
                    edu_span.append((edu_break[i - 1] + 1, edu_break[i]))

            parsing_order_self_pointing_token = []
            parsing_order_gold_pointing_token = []
            for x in parsing_order_self_pointing_edu:
                if x[0] == x[1] == x[2] == x[3]:
                    start_point = edu_span[x[0]][0]
                    end_point = edu_span[x[0]][1] + 1
                    parsing_order_self_pointing_token.append((start_point, end_point, end_point))
                else:
                    start_point = edu_span[x[0]][0]
                    split_point = edu_span[x[2]][0]
                    end_point = edu_span[x[3]][1] + 1
                    parsing_order_self_pointing_token.append((start_point, split_point, end_point))
                    parsing_order_gold_pointing_token.append((start_point, split_point, end_point))
            parsing_order_token = []
            # print(len(parsing_order_edu))
            # print(edu_span)
            # print(parsing_order_edu)
            for i, x in enumerate(parsing_order_edu):
                start_point = edu_span[x[0]][0]
                split_point = edu_span[x[2]][0]
                end_point = edu_span[x[3]][1] + 1
                parsing_order_token.append((start_point, split_point, end_point, parsing_label[i]))
            boundary_edu_break = [x + 1 for x in edu_break]
            boundary_sent_break = [x + 1 for x in sent_break]
            # parsing_order_token = []
            # for x in parsing_order_edu:
            #     start_leftspan = edu_span[x[0]][0]
            #     end_leftspan = edu_span[x[1]][1]
            #     start_rightspan = edu_span[x[2]][0]
            #     end_rightspan = edu_span[x[3]][1]
            #     parsing_order_token.append((start_leftspan, end_leftspan, start_rightspan, end_rightspan))
        else:
            # parsing_order_self_pointing_edu = [(0, 0, 0, 0)]
            # edu_span = [(0, edu_break[0])]
            # parsing_order_edu = []
            parsing_order_gold_pointing_token = []
            parsing_order_self_pointing_token = [(0, edu_break[0] + 1, edu_break[0] + 1)]
            parsing_order_token = []
            boundary_edu_break = [edu_break[0] + 1]
            boundary_sent_break = [sent_break[0] + 1]
            # parsing_label_self_pointing = ['None']
            # parsing_label = ['None']
        return boundary_sent_break, boundary_edu_break, parsing_order_token, parsing_order_gold_pointing_token, parsing_order_self_pointing_token

    @classmethod
    def build(cls, sequence):
        """
        Build a token-based discourse tree from the sequence.
        The sequence is generated in depth first search.


        Args:
            sequence (list[tuple]):
                A list of tuples used for generating a tree.
                Each tuple consits of the indices of left/right/split span boundaries,
                discourse label of the span.

        Returns:
            A result discourse tree.

        Examples:
            >>> sequence = [(0, 24, 30, 'Attribution_NS'), (0, 12, 24,'Joint_NN'), (0, 12, 12,'None'),
                            (12, 16, 24,'Attribution_SN'), (12, 16, 16,'None'),
                            (16, 24, 24,'None'), (24, 30, 30,'None')]
            >>> print(Tree.build(sequence))
            '(0:Nucleus=span:23,24:Satellite=Attribution:29)
            (0:Nucleus=Joint:11,12:Nucleus=Joint:23)
            (12:Satellite=Attribution:15,16:Nucleus=span:23)'
        """
        if len(sequence) == 0:
            return 'NONE'
        else:
            result = []
            for (i, k, j, label) in sequence:
                if k < j:
                    Nuclearity_left, Nuclearity_right, Relation_left, Relation_right \
                        = Label2RelationAndNucleus(label)
                    node = f'({i}:{Nuclearity_left}={Relation_left}:{k - 1},{k}:{Nuclearity_right}={Relation_right}:{j - 1})'
                result.append(node)
            return ' '.join(result)

    @classmethod
    def build_gold(cls, edu_break, gold_metric_edu):
        """
		Build a token-based discourse tree from gold edu_break and gold_metric_edu.


		Args:
		    edu_break (list(int)):
		        A list of edu breaking points
			gold_metric_edu (string)
				edu-based discourse treel

		Returns:
			A result discourse tree.

		Examples:
			>>> edu_break =[11, 15, 23, 29, 32, 40]
			>>> gold_metric_edu='(1:Nucleus=span:3,4:Satellite=Attribution:4)
			                    (1:Nucleus=Joint:1,2:Nucleus=Joint:3)
			                    (2:Satellite=Attribution:2,3:Nucleus=span:3)'
			>>> print(Tree.build_gold(edu_break, gold_metric_edu))
			'(0:Nucleus=span:23,24:Satellite=Attribution:29)
			(0:Nucleus=Joint:11,12:Nucleus=Joint:23)
			(12:Satellite=Attribution:15,16:Nucleus=span:23)'
		"""
        if gold_metric_edu == 'NONE':
            return 'NONE'
        else:
            edu_span = []
            for i in range(len(edu_break)):
                if i == 0:
                    edu_span.append((0, edu_break[0]))
                elif i < len(edu_break):
                    edu_span.append((edu_break[i - 1] + 1, edu_break[i]))
            result = []
            golden_metric_edu_split = re.split(' ', gold_metric_edu)
            for each_split in golden_metric_edu_split:
                left_start, Nuclearity_left, Relation_left, left_end, \
                right_start, Nuclearity_right, Relation_right, right_end = re.split(':|=|,', each_split[1:-1])
                left_start = int(left_start) - 1
                left_end = int(left_end) - 1
                right_start = int(right_start) - 1
                right_end = int(right_end) - 1
                node = f'({edu_span[left_start][0]}:{Nuclearity_left}={Relation_left}:{edu_span[left_end][1]},{edu_span[right_start][0]}:{Nuclearity_right}={Relation_right}:{edu_span[right_end][1]})'
                result.append(node)
            return ' '.join(result)

    def load(self, data, max_len=None, **kwargs):
        """
        Args:
            data (dict):
                A dictionary of 'sentence', 'gold_metric', 'edu_break'
            max_len (int):
                Sentences exceeding the length will be discarded. Default: ``None``.

        Returns:
            A list of TreeSentence instances.
        """
        # if isinstance(data, str):
        #     with open(data, 'r') as f:
        #         trees = [nltk.Tree.fromstring(string) for string in f]
        #     self.root = trees[0].label()
        # else:
        #     data = [data] if isinstance(data[0], str) else data
        #     trees = [self.totree(i, self.root) for i in data]
        assert isinstance(data, str)
        import pickle
        data_dict = pickle.load(open(data, "rb"))
        sents = data_dict['InputDocs']
        edu_break = data_dict['EduBreak_TokenLevel']
        sent_break = data_dict['SentBreak']
        golden_metric = data_dict['Docs_structure']

        i, sentences = 0, []
        for sent in progress_bar(sents, leave=False):
            # if len(tree) == 1 and not isinstance(tree[0][0], nltk.Tree):
            #     continue
            sentences.append(
                DiscourseTreeDocSentInfoToken(self, sent, sent_break[i], edu_break[i], ' '.join(golden_metric[i])))
            i += 1
        if max_len is not None:
            sentences = [i for i in sentences if len(i) < max_len]

        return sentences


class DiscourseTreeDocSentInfoToken(Sentence):
    """
    Args:
        transform (Tree):
            A Tree object.
        tree (nltk.tree.Tree):
            A nltk.Tree object.
    """

    def __init__(self, transform, sent, sent_break, edu_break, golden_metric):
        super().__init__(transform)

        # the values contain words, pos tags, raw trees, and spans
        # the tree is first left-binarized before factorized
        # spans are the factorization of tree traversed in pre-order
        self.values = [sent,
                       edu_break,
                       golden_metric,
                       *DiscourseTreeDocSentinfo.edu2token(golden_metric_edu=golden_metric, edu_break=edu_break,
                                                           sent_break=sent_break)]

    def __repr__(self):
        return self.values[-3].pformat(1000000)


class DiscourseTreeDocEduGold(Transform):
    """
    The Tree object factorize a constituency tree into four fields, each associated with one or more Field objects.

    Attributes:
        WORD:
            Words in the sentence.
        ORIGINAL_EDU_BREAK:
            EDU break in format of last indices of EDU segments (at token level)
        GOLD_METRIC:
            The raw discourse tree in string format (the constituents are at edu level).
        SENT_BREAK:
            Sentence break in format of last indices of EDU segments (at boundary level)
        EDU_BREAK:
            EDU break in format of last indices of EDU segments (at boundary level)
        PARSING_LABEL_TOKEN:
            The sequence of parsing label (start_index, split_index, end_index, discourse  label)
            (at boundary token level)
        PARSING_LABEL_EDU:
            The sequence of parsing label (start_index, split_index, end_index, discourse  label)
            (at boundary edu level)
        PARSING_ORDER_TOKEN:
            The sequence of parsing label (start_index, split_index, end_index)
            (at boundary token level)
        PARSING_ORDER_SELF_POINTING_TOKEN:
            The sequence of parsing label (start_index, split_index, end_index)
            at which we allow self pointing (in order to detect the edu)
            (at boundary token level)
        PARSING_ORDER_EDU:
            The sequence of parsing label (start_index, split_index, end_index)
            (at boundary edu level)
    """

    root = ''
    fields = ['WORD', 'ORIGINAL_EDU_BREAK', 'GOLD_METRIC',
              'SENT_BREAK', 'EDU_BREAK',
              'PARSING_LABEL_TOKEN', 'PARSING_LABEL_EDU',
              'PARSING_ORDER_EDU', 'PARSING_ORDER_TOKEN', 'PARSING_ORDER_SELF_POINTING_TOKEN'
              ]

    def __init__(self, WORD=None, ORIGINAL_EDU_BREAK=None, GOLD_METRIC=None,
                 SENT_BREAK=None, EDU_BREAK=None,
                 PARSING_LABEL_TOKEN=None, PARSING_LABEL_EDU=None,
                 PARSING_ORDER_EDU=None, PARSING_ORDER_TOKEN=None,
                 PARSING_ORDER_SELF_POINTING_TOKEN=None
                 ):
        super().__init__()

        self.WORD = WORD
        self.ORIGINAL_EDU_BREAK = ORIGINAL_EDU_BREAK
        self.GOLD_METRIC = GOLD_METRIC

        self.SENT_BREAK = SENT_BREAK
        self.EDU_BREAK = EDU_BREAK

        self.PARSING_LABEL_TOKEN = PARSING_LABEL_TOKEN
        self.PARSING_LABEL_EDU = PARSING_LABEL_EDU

        self.PARSING_ORDER_EDU = PARSING_ORDER_EDU
        self.PARSING_ORDER_TOKEN = PARSING_ORDER_TOKEN
        self.PARSING_ORDER_SELF_POINTING_TOKEN = PARSING_ORDER_SELF_POINTING_TOKEN

    @property
    def src(self):
        return self.WORD, self.GOLD_METRIC, self.ORIGINAL_EDU_BREAK, self.SENT_BREAK, self.EDU_BREAK

    @property
    def tgt(self):
        return self.PARSING_ORDER_TOKEN, self.PARSING_ORDER_SELF_POINTING_TOKEN, self.PARSING_ORDER_EDU, self.PARSING_LABEL_TOKEN, self.PARSING_LABEL_EDU

    @classmethod
    def edu2token(cls, golden_metric_edu, edu_break, sent_break):
        # This is to convert from raw data to computatonal data

        if not (golden_metric_edu == 'NONE'):
            parsing_order_edu = []
            parsing_label = []
            golden_metric_edu_split = re.split(' ', golden_metric_edu)
            assert (len(golden_metric_edu_split) == len(
                edu_break) - 1), f"something wrong {len(golden_metric_edu_split)}:{len(edu_break) - 1}"
            rootsplit = None
            for each_split in golden_metric_edu_split:
                left_start, Nuclearity_left, Relation_left, left_end, \
                right_start, Nuclearity_right, Relation_right, right_end = re.split(':|=|,', each_split[1:-1])
                left_start = int(left_start) - 1
                left_end = int(left_end) - 1
                right_start = int(right_start) - 1
                right_end = int(right_end) - 1
                relation_label = RelationAndNucleus2Label(Nuclearity_left,
                                                          Nuclearity_right,
                                                          Relation_left,
                                                          Relation_right)
                parsing_order_edu.append((left_start, left_end, right_start, right_end))
                if left_start == 0 and right_end == len(edu_break) - 1:
                    rootsplit = deepcopy((left_start, left_end, right_start, right_end))
                parsing_label.append(relation_label)

            # Now we add to the parsing edu the part that corresponding to edu detection component
            # (or when the case all the values)
            # in parsing order for edu are the same
            parsing_order_self_pointing_edu = []
            stacks = ['__StackRoot__', rootsplit]
            while stacks[-1] is not '__StackRoot__':
                stack_head = stacks[-1]
                assert (len(stack_head) == 4)
                parsing_order_self_pointing_edu.append(stack_head)
                if stack_head[0] == stack_head[1] and stack_head[2] == stack_head[3] and stack_head[2] == stack_head[1]:
                    del stacks[-1]
                elif stack_head[0] == stack_head[1] and stack_head[2] == stack_head[3]:
                    stack_top = (stack_head[0], stack_head[0], stack_head[0], stack_head[0])
                    stack_down = (stack_head[2], stack_head[2], stack_head[2], stack_head[2])
                    del stacks[-1]
                    stacks.append(stack_down)
                    stacks.append(stack_top)
                elif stack_head[0] == stack_head[1]:
                    stack_top = (stack_head[0], stack_head[0], stack_head[0], stack_head[0])
                    stack_down = [x for x in parsing_order_edu if x[0] == stack_head[2] and x[3] == stack_head[3]]
                    assert len(
                        stack_down) == 1, f"something wrong, \n{golden_metric_edu}, \n{edu_break}, \n{stack_down}"
                    stack_down = stack_down[0]
                    del stacks[-1]
                    stacks.append(stack_down)
                    stacks.append(stack_top)
                elif stack_head[2] == stack_head[3]:
                    stack_top = [x for x in parsing_order_edu if x[0] == stack_head[0] and x[3] == stack_head[1]]
                    stack_down = (stack_head[2], stack_head[2], stack_head[2], stack_head[2])
                    assert len(stack_top) == 1
                    stack_top = stack_top[0]
                    del stacks[-1]
                    stacks.append(stack_down)
                    stacks.append(stack_top)
                else:
                    stack_top = [x for x in parsing_order_edu if x[0] == stack_head[0] and x[3] == stack_head[1]]
                    stack_down = [x for x in parsing_order_edu if x[0] == stack_head[2] and x[3] == stack_head[3]]
                    assert len(stack_top) == 1 and len(stack_down) == 1
                    stack_top = stack_top[0]
                    stack_down = stack_down[0]
                    del stacks[-1]
                    stacks.append(stack_down)
                    stacks.append(stack_top)
            # parsing_label_self_pointing = []
            # for x in parsing_order_self_pointing_edu:
            #     if x in parsing_order_edu:
            #         parsing_label_self_pointing.append(parsing_label[parsing_order_edu.index(x)])
            #     else:
            #         parsing_label_self_pointing.append('None')
            edu_span = []
            for i in range(len(edu_break)):
                if i == 0:
                    edu_span.append((0, edu_break[0]))
                elif i < len(edu_break):
                    edu_span.append((edu_break[i - 1] + 1, edu_break[i]))

            parsing_order_token = []
            parsing_order_self_pointing_token = []
            parsing_order_edu_boundary = []
            for x in parsing_order_self_pointing_edu:
                if x[0] == x[1] == x[2] == x[3]:
                    start_point = edu_span[x[0]][0]
                    end_point = edu_span[x[0]][1] + 1
                    parsing_order_self_pointing_token.append((start_point, end_point, end_point))
                else:
                    start_point = edu_span[x[0]][0]
                    split_point = edu_span[x[2]][0]
                    end_point = edu_span[x[3]][1] + 1
                    parsing_order_self_pointing_token.append((start_point, split_point, end_point))
                    parsing_order_token.append((start_point, split_point, end_point))
                    parsing_order_edu_boundary.append((x[0], x[2], x[3] + 1))
            parsing_label_token = []
            parsing_label_edu = []
            # print(len(parsing_order_edu))
            # print(edu_span)
            # print(parsing_order_edu)
            for i, x in enumerate(parsing_order_edu):
                start_point = edu_span[x[0]][0]
                split_point = edu_span[x[2]][0]
                end_point = edu_span[x[3]][1] + 1
                parsing_label_token.append((start_point, split_point, end_point, parsing_label[i]))
                parsing_label_edu.append((x[0], x[2], x[3] + 1, parsing_label[i]))
            boundary_edu_break = [x + 1 for x in edu_break]
            boundary_sent_break = [x + 1 for x in sent_break]
            # parsing_order_token = []
            # for x in parsing_order_edu:
            #     start_leftspan = edu_span[x[0]][0]
            #     end_leftspan = edu_span[x[1]][1]
            #     start_rightspan = edu_span[x[2]][0]
            #     end_rightspan = edu_span[x[3]][1]
            #     parsing_order_token.append((start_leftspan, end_leftspan, start_rightspan, end_rightspan))
        else:
            # parsing_order_self_pointing_edu = [(0, 0, 0, 0)]
            # edu_span = [(0, edu_break[0])]
            # parsing_order_edu = []
            parsing_order_token = []
            parsing_order_edu_boundary = []
            parsing_order_self_pointing_token = [(0, edu_break[0] + 1, edu_break[0] + 1)]
            parsing_label_token = []
            parsing_label_edu = []
            boundary_edu_break = [edu_break[0] + 1]
            boundary_sent_break = [sent_break[0] + 1]
            # parsing_label_self_pointing = ['None']
            # parsing_label = ['None']
        return boundary_sent_break, boundary_edu_break, parsing_label_token, parsing_label_edu, \
               parsing_order_edu_boundary, parsing_order_token, parsing_order_self_pointing_token

    @classmethod
    def build(cls, sequence):
        """
        Build a token-based discourse tree from the sequence.
        The sequence is generated in depth first search.


        Args:
            sequence (list[tuple]):
                A list of tuples used for generating a tree.
                Each tuple consits of the indices of left/right/split span boundaries,
                discourse label of the span.

        Returns:
            A result discourse tree.

        Examples:
            >>> sequence = [(0, 24, 30, 'Attribution_NS'), (0, 12, 24,'Joint_NN'), (0, 12, 12,'None'),
                            (12, 16, 24,'Attribution_SN'), (12, 16, 16,'None'),
                            (16, 24, 24,'None'), (24, 30, 30,'None')]
            >>> print(Tree.build(sequence))
            '(0:Nucleus=span:23,24:Satellite=Attribution:29)
            (0:Nucleus=Joint:11,12:Nucleus=Joint:23)
            (12:Satellite=Attribution:15,16:Nucleus=span:23)'
        """
        if len(sequence) == 0:
            return 'NONE'
        else:
            result = []
            for (i, k, j, label) in sequence:
                if k < j:
                    Nuclearity_left, Nuclearity_right, Relation_left, Relation_right \
                        = Label2RelationAndNucleus(label)
                    node = f'({i}:{Nuclearity_left}={Relation_left}:{k - 1},{k}:{Nuclearity_right}={Relation_right}:{j - 1})'
                    result.append(node)
            return ' '.join(result)

    @classmethod
    def build_gold(cls, edu_break, gold_metric_edu):
        """
		Build a token-based discourse tree from gold edu_break and gold_metric_edu.


		Args:
		    edu_break (list(int)):
		        A list of edu breaking points
			gold_metric_edu (string)
				edu-based discourse treel

		Returns:
			A result discourse tree.

		Examples:
			>>> edu_break =[11, 15, 23, 29, 32, 40]
			>>> gold_metric_edu='(1:Nucleus=span:3,4:Satellite=Attribution:4)
			                    (1:Nucleus=Joint:1,2:Nucleus=Joint:3)
			                    (2:Satellite=Attribution:2,3:Nucleus=span:3)'
			>>> print(Tree.build_gold(edu_break, gold_metric_edu))
			'(0:Nucleus=span:23,24:Satellite=Attribution:29)
			(0:Nucleus=Joint:11,12:Nucleus=Joint:23)
			(12:Satellite=Attribution:15,16:Nucleus=span:23)'
		"""
        if gold_metric_edu == 'NONE':
            return 'NONE'
        else:
            edu_span = []
            for i in range(len(edu_break)):
                if i == 0:
                    edu_span.append((0, edu_break[0]))
                elif i < len(edu_break):
                    edu_span.append((edu_break[i - 1] + 1, edu_break[i]))
            result = []
            golden_metric_edu_split = re.split(' ', gold_metric_edu)
            for each_split in golden_metric_edu_split:
                left_start, Nuclearity_left, Relation_left, left_end, \
                right_start, Nuclearity_right, Relation_right, right_end = re.split(':|=|,', each_split[1:-1])
                left_start = int(left_start) - 1
                left_end = int(left_end) - 1
                right_start = int(right_start) - 1
                right_end = int(right_end) - 1
                node = f'({edu_span[left_start][0]}:{Nuclearity_left}={Relation_left}:{edu_span[left_end][1]},{edu_span[right_start][0]}:{Nuclearity_right}={Relation_right}:{edu_span[right_end][1]})'
                result.append(node)
            return ' '.join(result)

    def load(self, data, max_len=None, **kwargs):
        """
        Args:
            data (dict):
                A dictionary of 'sentence', 'gold_metric', 'edu_break'
            max_len (int):
                Sentences exceeding the length will be discarded. Default: ``None``.

        Returns:
            A list of TreeSentence instances.
        """
        # if isinstance(data, str):
        #     with open(data, 'r') as f:
        #         trees = [nltk.Tree.fromstring(string) for string in f]
        #     self.root = trees[0].label()
        # else:
        #     data = [data] if isinstance(data[0], str) else data
        #     trees = [self.totree(i, self.root) for i in data]
        assert isinstance(data, str)
        import pickle
        data_dict = pickle.load(open(data, "rb"))
        sents = data_dict['InputDocs']
        edu_break = data_dict['EduBreak_TokenLevel']
        sent_break = data_dict['SentBreak']
        golden_metric = data_dict['Docs_structure']

        i, sentences = 0, []
        for sent in progress_bar(sents, leave=False):
            # if len(tree) == 1 and not isinstance(tree[0][0], nltk.Tree):
            #     continue
            sentences.append(
                DiscourseTreeDocEduGoldSentence(self, sent, sent_break[i], edu_break[i], ' '.join(golden_metric[i])))
            i += 1
        if max_len is not None:
            sentences = [i for i in sentences if len(i) < max_len]

        return sentences


class DiscourseTreeDocEduGoldSentence(Sentence):
    """
    Args:
        transform (Tree):
            A Tree object.
        tree (nltk.tree.Tree):
            A nltk.Tree object.
    """

    def __init__(self, transform, sent, sent_break, edu_break, golden_metric):
        super().__init__(transform)

        # the values contain words, pos tags, raw trees, and spans
        # the tree is first left-binarized before factorized
        # spans are the factorization of tree traversed in pre-order
        self.values = [sent,
                       edu_break,
                       golden_metric,
                       *DiscourseTreeDocEduGold.edu2token(golden_metric_edu=golden_metric, edu_break=edu_break,
                                                          sent_break=sent_break)]

    def __repr__(self):
        return self.values[-5].pformat(1000000)