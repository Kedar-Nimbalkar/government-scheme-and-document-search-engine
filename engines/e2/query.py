"""Boolean parsing and sorted-set evaluation over positional postings."""

import re
from dataclasses import dataclass, field
from typing import TypeVar

from search_shared.tokenizer import tokenize, tokenize_with_spans

MAX_QUERY_LENGTH = 512
MAX_QUERY_TOKENS = 64
MAX_K = 50
MAX_TOP = 1000
PRECEDENCE = {"OR": 1, "AND": 2, "NOT": 3}
LEXER = re.compile(r'"[^"]*"|[()]|[^"()\s]+|"')


class InvalidQuery(ValueError):
    pass


class BooleanSyntax(ValueError):
    pass


@dataclass(frozen=True)
class Atom:
    terms: tuple[str, ...]
    quoted: bool = False
    acronym: bool = False
    source_span: tuple[int, int] | None = field(default=None, compare=False)


Token = Atom | str
T = TypeVar("T")


@dataclass
class Query:
    postfix: list[Token]
    positive: set[Atom]
    warning: str | None = None


def validate_page(k, page):
    if type(k) is not int or type(page) is not int or not 1 <= k <= MAX_K or page < 1 or page * k > MAX_TOP:
        raise InvalidQuery(f"k must be 1–{MAX_K}, page must be positive, and page × k must be at most {MAX_TOP}")


def _lex(query: str) -> tuple[list[Token], list[str], bool]:
    tokens = []
    keywords = []
    count = 0
    malformed = False
    for match in LEXER.finditer(query):
        raw = match[0]
        if raw in PRECEDENCE or raw in {"(", ")"}:
            tokens.append(raw)
            count += 1
        elif raw == '"':
            malformed = True
            count += 1
        else:
            phrase = raw.startswith('"')
            positioned = [] if phrase else tokenize_with_spans(raw)
            terms = tokenize(raw[1:-1]) if phrase else [term for term, _ in positioned]
            keywords.extend(terms)
            count += max(1, len(terms))
            if phrase:
                if not terms:
                    malformed = True
                else:
                    tokens.append(Atom(tuple(terms), quoted=True))
            else:
                boundaries = {0: match.start()}
                offset = 0
                for index, char in enumerate(raw, match.start() + 1):
                    offset += 2 if ord(char) > 0xFFFF else 1
                    boundaries[offset] = index
                for term, span in positioned:
                    start, end = boundaries[span["start"]], boundaries[span["end"]]
                    tokens.append(Atom((term,), acronym=len(term) > 1 and query[start:end].isupper(),
                                       source_span=(start, end) if len(terms) == 1 else None))
        if count > MAX_QUERY_TOKENS:
            raise InvalidQuery(f"Query exceeds {MAX_QUERY_TOKENS} tokens")
    return tokens, keywords, malformed


def _postfix(tokens: list[Token]) -> list[Token]:
    output = []
    operators = []
    expecting_operand = True

    def push(operator):
        while (operators and operators[-1] != "("
               and (PRECEDENCE[operators[-1]] > PRECEDENCE[operator]
                    or (PRECEDENCE[operators[-1]] == PRECEDENCE[operator] and operator != "NOT"))):
            output.append(operators.pop())
        operators.append(operator)

    for token in tokens:
        if isinstance(token, Atom):
            if not expecting_operand:
                push("OR")
            output.append(token)
            expecting_operand = False
        elif token == "(" or token == "NOT":
            if not expecting_operand:
                push("OR")
            if token == "(":
                operators.append(token)
            else:
                push(token)
            expecting_operand = True
        elif token == ")":
            if expecting_operand:
                raise BooleanSyntax
            while operators and operators[-1] != "(":
                output.append(operators.pop())
            if not operators:
                raise BooleanSyntax
            operators.pop()
        else:
            if expecting_operand:
                raise BooleanSyntax
            push(token)
            expecting_operand = True
    if expecting_operand or "(" in operators:
        raise BooleanSyntax
    output.extend(reversed(operators))
    return output


def positive_atoms(postfix: list[Token]) -> set[Atom]:
    stack: list[tuple[set[Atom], set[Atom]]] = []
    for token in postfix:
        if isinstance(token, Atom):
            stack.append(({token}, set()))
        elif token == "NOT":
            positive, negative = stack.pop()
            stack.append((negative, positive))
        else:
            right = stack.pop()
            left = stack.pop()
            stack.append((left[0] | right[0], left[1] | right[1]))
    return stack[0][0] if stack else set()


def parse(query: str) -> Query:
    if not isinstance(query, str) or not query.strip() or len(query) > MAX_QUERY_LENGTH:
        raise InvalidQuery(f"Query must contain 1–{MAX_QUERY_LENGTH} characters")
    try:
        query.encode("utf-8")
        tokens, keywords, malformed = _lex(query)
    except InvalidQuery:
        raise
    except ValueError as error:
        raise InvalidQuery("Query text could not be normalized") from error
    try:
        if malformed:
            raise BooleanSyntax
        postfix = _postfix(tokens)
    except BooleanSyntax:
        postfix = []
        for keyword in dict.fromkeys(keywords):
            postfix.append(Atom((keyword,)))
            if len(postfix) > 1:
                postfix.append("OR")
        return Query(postfix, positive_atoms(postfix), "BOOLEAN_FALLBACK")
    return Query(postfix, positive_atoms(postfix))


def sorted_merge(left: list[T], right: list[T], operator: str) -> list[T]:
    """Apply AND (intersection), OR (union), or NOT (left difference)."""
    result = []
    i = j = 0
    while i < len(left) and j < len(right):
        if left[i] < right[j]:
            if operator != "AND":
                result.append(left[i])
            i += 1
        elif right[j] < left[i]:
            if operator == "OR":
                result.append(right[j])
            j += 1
        else:
            if operator != "NOT":
                result.append(left[i])
            i += 1
            j += 1
    if operator != "AND":
        result.extend(left[i:])
    if operator == "OR":
        result.extend(right[j:])
    return result


def phrase_starts(position_lists: list[list[int]]) -> list[int]:
    """Keep starts whose position plus every query offset appears in that term."""
    starts = position_lists[0]
    for offset, positions in enumerate(position_lists[1:], 1):
        starts = sorted_merge(starts, [position - offset for position in positions], "AND")
        if not starts:
            break
    return starts


def evaluate(postfix: list[Token], eligible: list[int], operands: dict[Atom, list[int]]) -> list[int]:
    stack = []
    for token in postfix:
        if isinstance(token, Atom):
            stack.append(operands[token])
        elif token == "NOT":
            stack.append(sorted_merge(eligible, stack.pop(), "NOT"))
        else:
            right = stack.pop()
            stack.append(sorted_merge(stack.pop(), right, token))
    return stack[0] if stack else []
