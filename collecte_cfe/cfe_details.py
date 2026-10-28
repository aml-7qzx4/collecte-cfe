"""Lecture des éléments de calcul d'un avis CFE et explication de l'écart avec l'année précédente.

La page « Cotisation foncière des entreprises » de l'avis numérote chaque élément (1 à 196).
pypdf, en mode « layout », sépare les colonnes d'au moins deux espaces et les milliers d'un seul :
chaque valeur suit le numéro de la ligne à laquelle elle appartient.
"""

import re
from dataclasses import asdict, dataclass, field
from decimal import Decimal
from pathlib import Path

from pypdf import PdfReader


@dataclass(frozen=True)
class Levy:
    """One column of the calculation: a rate voted by a local authority, or an additional tax (TSE, GEMAPI)."""

    rate: str
    previous_rate: str | None = None
    base: int | None = None
    contribution: int | None = None

    @property
    def additional(self) -> bool:
        # Additional taxes print their rate with five decimals, local authorities with two.
        return len(self.rate.split(',')[1]) >= 4


@dataclass(frozen=True)
class CfeDetails:
    rental_value: int | None = None
    base_after_reductions: int | None = None
    minimum_base: bool | None = None
    turnover_band: str | None = None
    part_year: bool | None = None
    minimum_base_amount: int | None = None
    exempt_base: int | None = None
    levies: tuple[Levy, ...] = field(default_factory=tuple)
    contributions: int | None = None
    management_fees: int | None = None
    cfe_total: int | None = None
    chambers: int | None = None
    tax_total: int | None = None

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> 'CfeDetails':
        return cls(**{**data, 'levies': tuple(Levy(**levy) for levy in data.get('levies', ()))})

    @property
    def base(self) -> int | None:
        """Taxable base of the local authority, after exemptions."""
        for levy in self.levies:
            if not levy.additional and levy.base is not None:
                return levy.base
        return next((levy.base for levy in self.levies if levy.base is not None), None)


_LABEL = re.compile(r'(\d{1,3}) ?-(?:\s|$)')
_AMOUNT = re.compile(r'\d{1,3}(?: \d{3})*')
_RATE = re.compile(r'\d+,\d+')


def _runs(line: str) -> list[tuple[int, str]]:
    return [(m.start(), m.group()) for m in re.finditer(r'\S+(?: \S+)*', line)]


def _numbered_values(text: str) -> dict[int, list[tuple[int, str]]]:
    """Values printed after each numbered label; a line may hold two labels (left and right halves)."""
    values: dict[int, list[tuple[int, str]]] = {}
    for line in text.splitlines():
        current = None
        for offset, run in _runs(line):
            label = _LABEL.match(run)
            if label:
                current = int(label.group(1))
                values.setdefault(current, [])
                # « 27 - » may stand alone before its title: the title is not a value.
                continue
            if current is not None:
                values[current].append((offset, run))
    return values


def _amounts(values: list[tuple[int, str]]) -> list[int]:
    found = []
    for _offset, run in values:
        run = run.removeprefix('MIN').strip()
        if _AMOUNT.fullmatch(run):
            found.append(int(run.replace(' ', '')))
    return found


def _amount(values: dict, number: int) -> int | None:
    amounts = _amounts(values.get(number, []))
    return amounts[-1] if amounts else None


def _rates(values: list[tuple[int, str]]) -> list[tuple[int, str]]:
    return [(offset, rate) for offset, run in values for rate in _RATE.findall(run)]


def _flag(values: dict, number: int) -> bool | None:
    for _offset, run in values.get(number, []):
        if run in ('OUI', 'NON'):
            return run == 'OUI'
    return None


def _labelled_total(text: str) -> int | None:
    """« MONTANT DE VOTRE IMPÔT » without its line number, as some years print it."""
    found = re.findall(r'MONTANT DE VOTRE IMP\S*\s+(\d{1,3}(?: \d{3})*)(?!\d)', text)
    return int(found[-1].replace(' ', '')) if found else None


def detail_page_text(path: Path | str) -> str | None:
    reader = PdfReader(path)
    try:
        for page in reader.pages:
            text = page.extract_text(extraction_mode='layout') or ''
            if 'Bases nettes' in text and 'MONTANT DE VOTRE IMP' in text:
                return text
        return None
    finally:
        reader.close()


def parse_details(text: str) -> CfeDetails:
    values = _numbered_values(text)
    rates = _rates(values.get(17, []))
    previous = _rates(values.get(16, []))
    bases = _amounts(values.get(15, []))
    contributions = _amounts(values.get(19, [])) or _amounts(values.get(18, []))
    previous_by_column = {}
    for offset, rate in previous:
        # The reminder of last year's rate sits above the column it belongs to.
        column = min(range(len(rates)), key=lambda i: abs(rates[i][0] - offset), default=None)
        if column is not None:
            previous_by_column[column] = rate
    levies = tuple(
        Levy(
            rate,
            previous_by_column.get(i),
            bases[i] if len(bases) == len(rates) else None,
            contributions[i] if len(contributions) == len(rates) else None,
        )
        for i, (_offset, rate) in enumerate(rates)
    )
    band = next((run for _offset, run in values.get(10, []) if re.search(r'\d', run)), None)
    chambers = [amount for amount in (_amount(values, 35), _amount(values, 45)) if amount is not None]
    exempt = _amounts(values.get(13, []))
    return CfeDetails(
        rental_value=_amount(values, 1),
        base_after_reductions=_amount(values, 8),
        minimum_base=_flag(values, 9),
        turnover_band=' '.join(band.split()) if band else None,
        part_year=_flag(values, 11),
        minimum_base_amount=_amount(values, 12),
        exempt_base=max(exempt) if exempt else None,
        levies=levies,
        contributions=_amount(values, 23),
        management_fees=_amount(values, 24),
        cfe_total=_amount(values, 25),
        chambers=sum(chambers) if chambers else None,
        tax_total=_tax_total(values, text),
    )


def _tax_total(values: dict, text: str) -> int | None:
    # From 2025 the tax is line 196 (194 being the total of contributions, the same amount); in 2024 it was line 194.
    for number in (196, 194):
        amount = _amount(values, number)
        if amount is not None:
            return amount
    return _labelled_total(text)


def read_details(path: Path | str) -> CfeDetails | None:
    text = detail_page_text(path)
    return parse_details(text) if text else None


# --- Explanation -----------------------------------------------------------------------------


@dataclass(frozen=True)
class Reason:
    text: str
    amount: int | None = None


@dataclass(frozen=True)
class Explanation:
    current_total: int | None
    previous_total: int | None
    reasons: tuple[Reason, ...]
    previous_read: bool = False

    @property
    def delta(self) -> int | None:
        if self.current_total is None or self.previous_total is None:
            return None
        return self.current_total - self.previous_total

    @property
    def summary(self) -> str:
        delta = self.delta
        if not self.previous_read:
            return 'Avis de l’année précédente non lu : seules les hausses de taux sont expliquées.'
        if delta is None:
            return 'Montant total illisible sur l’un des deux avis : écart non chiffré.'
        if delta == 0:
            return f'Montant stable : {euros(self.current_total)}.'
        change = f' ({share(delta / self.previous_total)})' if self.previous_total else ''
        return f'{"Hausse" if delta > 0 else "Baisse"} de {euros(abs(delta))}{change} : {euros(self.previous_total)} → {euros(self.current_total)}.'


def euros(amount: int | None) -> str:
    if amount is None:
        return 'non lu'
    return f'{amount:,} €'.replace(',', ' ')


def share(ratio: float) -> str:
    return f'{ratio * 100:+.1f} %'.replace('.', ',')


def signed(amount: int) -> str:
    return f'{"+" if amount >= 0 else "−"}{euros(abs(amount))}'


def percent(rate: str) -> str:
    return f'{rate} %'


def _rate(value: str) -> Decimal:
    return Decimal(value.replace(',', '.'))


def _effect(base: int | None, rate: Decimal) -> int:
    return round((base or 0) * rate / 100)


def _authority(levy: Levy, index: int, levies: tuple[Levy, ...]) -> str:
    if levy.additional:
        return 'Taxes additionnelles (TSE, GEMAPI)'
    local = [item for item in levies if not item.additional]
    return (
        'Taux voté par la commune ou l’intercommunalité'
        if len(local) == 1
        else f'Taux local n° {local.index(levy) + 1}'
    )


def explain(current: CfeDetails, previous: CfeDetails | None) -> Explanation:
    reasons: list[Reason] = []
    previous_total = previous.tax_total if previous else None
    aligned = (
        bool(previous)
        and len(previous.levies) == len(current.levies)
        and all(a.additional == b.additional for a, b in zip(current.levies, previous.levies))
    )

    if previous:
        if previous.exempt_base and not current.exempt_base and (previous.base or 0) == 0 and current.base:
            reasons.append(Reason(f'Fin d’exonération : la base imposable passe de 0 € à {euros(current.base)}.'))
        elif previous.part_year and not current.part_year:
            reasons.append(
                Reason('L’an dernier, l’activité couvrait moins de 9 mois : la base était réduite au prorata.')
            )
        if current.minimum_base and previous.minimum_base:
            if current.turnover_band != previous.turnover_band and current.turnover_band and previous.turnover_band:
                reasons.append(
                    Reason(
                        f'Tranche de chiffre d’affaires retenue pour la base minimum : {previous.turnover_band} → {current.turnover_band}.'
                    )
                )
            if current.minimum_base_amount != previous.minimum_base_amount and None not in (
                current.minimum_base_amount,
                previous.minimum_base_amount,
            ):
                reasons.append(
                    Reason(
                        f'Base minimum : {euros(previous.minimum_base_amount)} → {euros(current.minimum_base_amount)}.'
                    )
                )
        elif current.minimum_base and previous.minimum_base is False:
            reasons.append(
                Reason(
                    f'Imposition sur la base minimum cette année ({euros(current.minimum_base_amount)}), au lieu de la valeur locative.'
                )
            )
        elif previous.minimum_base and current.minimum_base is False:
            reasons.append(Reason('Imposition sur la valeur locative cette année, au lieu de la base minimum.'))
        elif (
            current.rental_value != previous.rental_value
            and None not in (current.rental_value, previous.rental_value)
            and previous.rental_value
        ):
            change = (current.rental_value - previous.rental_value) / previous.rental_value
            reasons.append(
                Reason(
                    f'Valeur locative : {euros(previous.rental_value)} → {euros(current.rental_value)} ({share(change)}).'
                )
            )
        if aligned:
            base_effect = sum(
                _effect((levy.base or 0) - (old.base or 0), _rate(old.rate))
                for levy, old in zip(current.levies, previous.levies)
            )
        else:
            old_rate = sum((_rate(levy.rate) for levy in previous.levies), Decimal(0))
            base_effect = _effect((current.base or 0) - (previous.base or 0), old_rate)
        if base_effect and current.base != previous.base:
            reasons.append(Reason(f'Effet de la base ({euros(previous.base)} → {euros(current.base)})', base_effect))

    rate_effect = 0
    for index, levy in enumerate(current.levies):
        old = previous.levies[index].rate if aligned else levy.previous_rate
        if old is None or _rate(old) == _rate(levy.rate):
            continue
        effect = _effect(levy.base, _rate(levy.rate) - _rate(old))
        rate_effect += effect
        reasons.append(
            Reason(f'{_authority(levy, index, current.levies)} : {percent(old)} → {percent(levy.rate)}', effect)
        )

    if previous:
        for label, now, before in (
            ('Frais de gestion', current.management_fees, previous.management_fees),
            ('Taxes pour frais de chambres consulaires', current.chambers, previous.chambers),
        ):
            if (now or 0) != (before or 0):
                reasons.append(Reason(label, (now or 0) - (before or 0)))
        explained = sum(reason.amount or 0 for reason in reasons)
        rest = (current.tax_total or 0) - (previous.tax_total or 0) - explained
        if previous.tax_total is not None and current.tax_total is not None and abs(rest) >= 2:
            reasons.append(Reason('Autres effets (lissage, réductions, arrondis)', rest))
    return Explanation(current.tax_total, previous_total, tuple(reasons), previous is not None)


def explanation_lines(explanation: Explanation) -> list[str]:
    lines = [explanation.summary]
    for reason in explanation.reasons:
        lines.append(f'• {reason.text}' + (f' : {signed(reason.amount)}' if reason.amount else ''))
    if explanation.delta is not None and not explanation.reasons and explanation.delta:
        lines.append('• Aucun élément de calcul ne change : vérifiez l’avis.')
    return lines
