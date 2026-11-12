"""Service integrating open academic & quiz APIs:
1. OpenAlex & Semantic Scholar (Papers, theses, research)
2. Open Library & Gutendex (Books, textbooks, classics)
3. Open Trivia Database (International quiz questions)
"""

import html
import json
import logging
import random
import urllib.parse
import urllib.request
from typing import Any

logger = logging.getLogger(__name__)

USER_AGENT = "ExamSystem/1.0 (Educational Academic Portal; contact: admin@examsystem.local)"


def _fetch_json(url: str, timeout: int = 8) -> dict[str, Any] | None:
    try:
        req = urllib.request.Request(
            url,
            headers={
                "User-Agent": USER_AGENT,
                "Accept": "application/json",
            },
        )
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            if resp.status == 200:
                return json.loads(resp.read().decode("utf-8"))
    except Exception as e:
        logger.warning("Error fetching %s: %s", url, e)
    return None


def search_research_papers(query: str, limit: int = 10, page: int = 1) -> list[dict[str, Any]]:
    """Search research papers and academic works using OpenAlex with Semantic Scholar fallback."""
    if not query.strip():
        query = "education"

    encoded = urllib.parse.quote_plus(query.strip())
    results: list[dict[str, Any]] = []

    # 1. Primary: OpenAlex API (fast, open, comprehensive)
    openalex_url = f"https://api.openalex.org/works?search={encoded}&per-page={limit}&page={page}"
    data = _fetch_json(openalex_url, timeout=7)
    if data and "results" in data:
        for item in data.get("results", []):
            authors = [
                auth.get("author", {}).get("display_name", "")
                for auth in item.get("authorships", [])[:4]
                if auth.get("author", {}).get("display_name")
            ]
            
            # Abstract inverted index decoding if available
            abstract = ""
            inv_idx = item.get("abstract_inverted_index")
            if inv_idx and isinstance(inv_idx, dict):
                word_positions: list[tuple[int, str]] = []
                for word, pos_list in inv_idx.items():
                    for pos in pos_list:
                        word_positions.append((pos, word))
                word_positions.sort(key=lambda x: x[0])
                abstract = " ".join(w for _, w in word_positions)
                if len(abstract) > 350:
                    abstract = abstract[:350] + "..."

            open_access = item.get("open_access", {}) or {}
            pdf_url = open_access.get("oa_url") or item.get("primary_location", {}).get("pdf_url")
            landing_url = item.get("doi") or item.get("id")

            results.append({
                "id": item.get("id", ""),
                "title": item.get("display_name") or item.get("title") or "Nghiên cứu học thuật",
                "authors": ", ".join(authors) if authors else "Nhiều tác giả",
                "year": item.get("publication_year") or 2024,
                "abstract": abstract or "Tài liệu học thuật toàn văn, bao quát các nghiên cứu và thực nghiệm chuyên sâu.",  # noqa: E501
                "pdf_url": pdf_url,
                "doi": item.get("doi"),
                "landing_url": landing_url,
                "citations": item.get("cited_by_count", 0),
                "source": "OpenAlex / Academic Repositories",
            })

    # 2. Fallback / supplementary: Semantic Scholar
    if not results:
        ss_url = f"https://api.semanticscholar.org/graph/v1/paper/search?query={encoded}&limit={limit}&fields=title,authors,abstract,year,openAccessPdf,citationCount"
        ss_data = _fetch_json(ss_url, timeout=6)
        if ss_data and "data" in ss_data:
            for item in ss_data.get("data", []):
                authors = [a.get("name") for a in item.get("authors", []) if a.get("name")]
                pdf_info = item.get("openAccessPdf") or {}
                results.append({
                    "id": item.get("paperId", ""),
                    "title": item.get("title") or "Bài báo khoa học",
                    "authors": ", ".join(authors) if authors else "Tác giả học thuật",
                    "year": item.get("year") or 2024,
                    "abstract": item.get("abstract") or "Tóm tắt nghiên cứu khoa học chuyên đề.",
                    "pdf_url": pdf_info.get("url"),
                    "landing_url": f"https://www.semanticscholar.org/paper/{item.get('paperId')}",
                    "citations": item.get("citationCount", 0),
                    "source": "Semantic Scholar (Allen AI)",
                })

    return results


def search_books(query: str, limit: int = 12, page: int = 1) -> list[dict[str, Any]]:
    """Search e-books, textbooks and classics using Open Library API."""
    if not query.strip():
        query = "science"

    encoded = urllib.parse.quote_plus(query.strip())
    results: list[dict[str, Any]] = []

    # 1. Open Library API
    ol_url = f"https://openlibrary.org/search.json?q={encoded}&limit={limit}&page={page}"
    data = _fetch_json(ol_url, timeout=7)
    if data and "docs" in data:
        for doc in data.get("docs", []):
            cover_i = doc.get("cover_i")
            cover_url = f"https://covers.openlibrary.org/b/id/{cover_i}-M.jpg" if cover_i else None
            key = doc.get("key", "")
            read_url = f"https://openlibrary.org{key}" if key else None
            
            authors = doc.get("author_name", [])
            publishers = doc.get("publisher", [])

            results.append({
                "title": doc.get("title") or "Sách giáo trình điện tử",
                "author": ", ".join(authors[:2]) if authors else "Nhiều tác giả",
                "year": doc.get("first_publish_year") or (doc.get("publish_year", [2022])[0] if doc.get("publish_year") else 2022),  # noqa: E501
                "cover_url": cover_url,
                "read_url": read_url,
                "publisher": publishers[0] if publishers else "NXB Giáo Dục / Quốc Tế",
                "subjects": ", ".join(doc.get("subject", [])[:3]),
                "editions_count": doc.get("edition_count", 1),
                "source": "Open Library (Internet Archive)",
            })

    return results


OPEN_TRIVIA_CATEGORIES = [
    {"id": 0, "name": "✨ Tất cả chủ đề", "icon": "🌐"},
    {"id": 17, "name": "Khoa học & Tự nhiên", "icon": "🔬"},
    {"id": 18, "name": "Máy tính & Công nghệ", "icon": "💻"},
    {"id": 19, "name": "Toán học & Logic", "icon": "📐"},
    {"id": 23, "name": "Lịch sử Thế giới", "icon": "🏛️"},
    {"id": 22, "name": "Địa lí & Văn hóa", "icon": "🗺️"},
    {"id": 9, "name": "Kiến thức Đại cương", "icon": "🧠"},
    {"id": 10, "name": "Văn học & Sách", "icon": "📚"},
]


def get_open_trivia_quizzes(amount: int = 10, category_id: int = 0, difficulty: str = "") -> list[dict[str, Any]]:
    """Fetch structured quiz questions from Open Trivia Database API."""
    url = f"https://opentdb.com/api.php?amount={amount}&type=multiple"
    if category_id and int(category_id) > 0:
        url += f"&category={category_id}"
    if difficulty in ("easy", "medium", "hard"):
        url += f"&difficulty={difficulty}"

    data = _fetch_json(url, timeout=7)
    results: list[dict[str, Any]] = []

    if data and data.get("response_code") == 0 and "results" in data:
        for idx, item in enumerate(data.get("results", []), start=1):
            question_text = html.unescape(item.get("question", ""))
            correct = html.unescape(item.get("correct_answer", ""))
            incorrects = [html.unescape(inc) for inc in item.get("incorrect_answers", [])]
            
            all_choices = incorrects + [correct]
            random.shuffle(all_choices)

            letters = ["A", "B", "C", "D"]
            correct_letter = letters[all_choices.index(correct)] if correct in all_choices else "A"

            results.append({
                "number": idx,
                "category": html.unescape(item.get("category", "General")),
                "difficulty": item.get("difficulty", "medium").capitalize(),
                "question": question_text,
                "choices": [
                    {"letter": letters[i], "text": all_choices[i]}
                    for i in range(min(len(letters), len(all_choices)))
                ],
                "correct_letter": correct_letter,
                "correct_answer": correct,
            })

    return results
