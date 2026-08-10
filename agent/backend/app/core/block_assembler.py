"""
BlockAssembler: SQL Pipeline Assembly Engine

Loads Block YAML definitions and Recipe YAML definitions, then assembles
complete SQL queries by chaining CTE blocks according to the recipe.

Architecture:
- BlockLibrary: loads block/*.yaml, each block defines a CTE template with parameterizable parts
- RecipeLibrary: loads recipes/*.yaml, each recipe defines block sequence + variant config
- BlockAssembler: assembles final SQL by expanding blocks, resolving params, and appending final SELECT
"""

import os
import re
import yaml
from pathlib import Path
from typing import Dict, Any, Optional, List, Tuple

CORE_DIR = Path(__file__).parent
BLOCKS_DIR = CORE_DIR / "blocks"
RECIPES_DIR = CORE_DIR / "recipes"

CTE_SEP = ",\n\n"


class BlockLibrary:
    """Load and manage SQL block definitions from YAML files."""
    
    def __init__(self, blocks_dir: str = None):
        self.blocks_dir = Path(blocks_dir) if blocks_dir else BLOCKS_DIR
        self._blocks: Dict[str, dict] = {}
        self._load_all()
    
    def _load_all(self):
        for f in sorted(self.blocks_dir.glob("*.yaml")):
            name = f.stem
            with open(f, 'r', encoding='utf-8') as fh:
                self._blocks[name] = yaml.safe_load(fh)
    
    def get(self, name: str) -> Optional[dict]:
        return self._blocks.get(name)
    
    def list_blocks(self) -> List[str]:
        return list(self._blocks.keys())


class RecipeLibrary:
    """Load and manage recipe definitions from YAML files."""
    
    def __init__(self, recipes_dir: str = None):
        self.recipes_dir = Path(recipes_dir) if recipes_dir else RECIPES_DIR
        self._recipes: Dict[str, dict] = {}
        self._load_all()
    
    def _load_all(self):
        for f in sorted(self.recipes_dir.glob("*.yaml")):
            name = f.stem
            with open(f, 'r', encoding='utf-8') as fh:
                self._recipes[name] = yaml.safe_load(fh)
        # 也加载用户策略（用户策略同名时覆盖系统 recipe）
        user_dir = self.recipes_dir.parent / "user_strategies"
        if user_dir.exists():
            for f in sorted(user_dir.glob("*.yaml")):
                name = f.stem
                with open(f, 'r', encoding='utf-8') as fh:
                    self._recipes[name] = yaml.safe_load(fh)
    
    def get(self, name: str) -> Optional[dict]:
        return self._recipes.get(name)
    
    def list_recipes(self) -> List[str]:
        return list(self._recipes.keys())
    
    def find_recipe_for_tag(self, tag_name: str) -> Optional[Tuple[str, str]]:
        """Find recipe name and variant that produces the given output tag_name."""
        for rname, recipe in self._recipes.items():
            for vname, variant in recipe.get('variants', {}).items():
                if variant.get('output_tag_name') == tag_name:
                    return rname, vname
        return None


class BlockAssembler:
    """Assemble complete SQL from a recipe + variant + NL-derived parameters."""
    
    def __init__(self, block_lib: BlockLibrary = None, recipe_lib: RecipeLibrary = None):
        self.block_lib = block_lib or BlockLibrary()
        self.recipe_lib = recipe_lib or RecipeLibrary()
    
    def assemble(self, recipe_name: str, variant_name: str, 
                 extra_params: Dict[str, Any] = None) -> str:
        """
        Assemble a complete SQL query from a recipe + variant.
        
        Args:
            recipe_name: Recipe name (e.g., 'conflict_pipeline')
            variant_name: Variant name (e.g., 'vehicle' or 'vru')
            extra_params: Additional parameters from NL understanding (overrides variant defaults)
        
        Returns:
            Complete SQL string with WITH ... SELECT
        """
        recipe = self.recipe_lib.get(recipe_name)
        if not recipe:
            raise ValueError(f"Recipe not found: {recipe_name}")
        
        # ── Resolve variant: prefer named variant, fallback to recipe top-level ──
        variants = recipe.get('variants', {})
        if variants and variant_name in variants:
            variant = variants[variant_name]
        elif variants:
            # Try first available variant
            variant = next(iter(variants.values()), {})
        else:
            # No variants defined — use recipe top-level as "virtual variant"
            variant = {k: v for k, v in recipe.items()
                       if k not in ('name', 'version', 'description', 'blocks',
                                    'variants', 'final_select_template')}
        
        # ── Block path: if blocks defined, use block assembly ──
        blocks = recipe.get('blocks', [])
        if blocks:
            # Block assembly path — blocks take priority over raw_sql
            params = dict(variant)
            if extra_params:
                params.update(extra_params)
            params = self._resolve_dict_vars(params)
            cte_parts = self._assemble_blocks(recipe, params)
            final_select = recipe.get('final_select_template', '')
            final_select = self._resolve_str(final_select, params)
            if cte_parts:
                cte_sql = CTE_SEP.join(cte_parts)
                full_sql = "WITH\n" + cte_sql + "\n\n" + final_select
            else:
                full_sql = final_select
            return full_sql

        # ── Fast path: raw_sql (production SQL pass-through) ──
        raw_sql = variant.get('raw_sql')
        if raw_sql:
            params = dict(variant)
            if extra_params:
                params.update(extra_params)
            params = self._resolve_dict_vars(params)
            return self._resolve_str(raw_sql, params)
        
        # Build base params from variant
        params = dict(variant)
        if extra_params:
            params.update(extra_params)
        
        # Iteratively resolve all template vars in params
        params = self._resolve_dict_vars(params)
        
        # Assemble CTEs
        cte_parts = self._assemble_blocks(recipe, params)
        
        # Assemble final SELECT
        final_select = recipe.get('final_select_template', '')
        final_select = self._resolve_str(final_select, params)
        
        # Combine into complete SQL
        if cte_parts:
            cte_sql = CTE_SEP.join(cte_parts)
            full_sql = "WITH\n" + cte_sql + "\n\n" + final_select
        else:
            full_sql = final_select
        
        return full_sql

    def _assemble_blocks(self, recipe: dict, params: Dict[str, Any]) -> list:
        """Assemble CTE parts from a recipe's block definitions."""
        cte_parts = []
        for block_def in recipe['blocks']:
            block_params = dict(params)
            if 'params' in block_def:
                for k, v in block_def['params'].items():
                    block_params[k] = self._resolve_str(str(v), params)
            
            if block_def.get('custom'):
                sql = block_def['sql_template']
                sql = self._resolve_str(sql, block_params)
                cte_parts.append(sql.strip())
            else:
                block = self.block_lib.get(block_def['name'])
                if not block:
                    raise ValueError(f"Block not found: {block_def['name']}")
                
                if 'parameters' in block:
                    for pname, pdef in block['parameters'].items():
                        if pname not in block_params and 'default' in pdef:
                            block_params[pname] = pdef['default']
                
                if 'variants' in block:
                    variant_key = block_params.get('conflict_mode') or block_params.get('target_type') or block_params.get('filter_mode')
                    if variant_key and variant_key in block['variants']:
                        for vk, vv in block['variants'][variant_key].items():
                            if vk not in block_params:
                                block_params[vk] = vv
                
                if block_def['name'] == 'proximity_analysis':
                    target_type = block_params.get('target_type', 'vehicle')
                    if target_type == 'vru':
                        vru_types = block_params.get('vru_types', ["pedestrian","cyclist","motorcycle","stroller","wheelchair","animal"])
                        vru_types_str = "','".join(vru_types)
                        block_params['type_expr'] = f"CASE WHEN d.type IN ('{vru_types_str}') THEN d.type ELSE NULL END"
                    else:
                        block_params['type_expr'] = "GROUP_CONCAT(DISTINCT d.type)"
                
                cte_name = block_def.get('cte_name', block['name'])
                cte_name = self._resolve_str(str(cte_name), block_params)
                block_params['cte_name'] = cte_name
                
                if 'upstream' in block_def:
                    block_params['upstream'] = self._resolve_str(str(block_def['upstream']), block_params)
                if 'speed_cte' in block_def:
                    block_params['speed_cte'] = self._resolve_str(str(block_def['speed_cte']), block_params)
                if 'prox_cte' in block_def:
                    block_params['prox_cte'] = self._resolve_str(str(block_def['prox_cte']), block_params)
                
                sql = block['sql_template']
                sql = self._resolve_str(sql, block_params)
                cte_parts.append(sql.strip())
        
        return cte_parts

    def assemble_hybrid(self, recipe_name: str, variant_name: str,
                        auto_blocks: list, custom_ctes_sql: list,
                        final_select_sql: str) -> str:
        """混合拼装：已知block自动拼装 + LLM生成的custom CTE + final SELECT。

        用于Layer 3场景：用户查询没有完全匹配的recipe，但可以组合
        已有block + LLM生成的胶水CTE来拼装SQL。

        Args:
            recipe_name: 虚拟recipe名（用于日志）
            variant_name: 通常是"default"
            auto_blocks: list of block定义dict，格式同recipe['blocks']
                         [{"name": "continuous_segment", "params": {...}, "cte_name": "seg1"}]
            custom_ctes_sql: list of str，每个是一个完整CTE（含CTE名 AS (...)）
            final_select_sql: str，最终SELECT语句

        Returns:
            完整SQL字符串
        """
        cte_parts = []

        # Step A: 自动拼装已知block
        for block_def in auto_blocks:
            block = self.block_lib.get(block_def["name"])
            if not block:
                raise ValueError(f"Block not found: {block_def['name']}")

            block_params = {}
            if "params" in block_def:
                for k, v in block_def["params"].items():
                    block_params[k] = str(v)

            # Merge default params from block definition
            if "parameters" in block:
                for pname, pdef in block["parameters"].items():
                    if pname not in block_params and "default" in pdef:
                        block_params[pname] = pdef["default"]

            cte_name = block_def.get("cte_name", block["name"])
            block_params["cte_name"] = cte_name

            if "upstream" in block_def:
                block_params["upstream"] = block_def["upstream"]

            sql = block["sql_template"]
            sql = self._resolve_str(sql, block_params)
            cte_parts.append(sql.strip())

        # Step B: 添加LLM生成的custom CTEs
        cte_parts.extend([c.strip() for c in custom_ctes_sql if c.strip()])

        # Step C: 拼装
        if cte_parts:
            cte_sql = CTE_SEP.join(cte_parts)
            full_sql = "WITH\n" + cte_sql + "\n\n" + final_select_sql
        else:
            full_sql = final_select_sql

        return full_sql
    
    def _resolve_str(self, template: str, params: Dict[str, Any]) -> str:
        """Replace {var} placeholders in a string with params values.
        
        If a placeholder key is not found in params, it is replaced with
        an empty string (rather than leaving the raw {key} in the output).
        This prevents unresolved placeholders from surviving into final SQL.
        """
        if not isinstance(template, str):
            return str(template)
        
        for _ in range(5):  # Multiple passes for nested placeholders
            def replacer(match):
                key = match.group(1)
                val = params.get(key, "")  # Default to empty string for unresolved
                if isinstance(val, list):
                    return ', '.join(str(v) for v in val)
                return str(val)
            
            result = re.sub(r'\{(\w+)\}', replacer, template)
            if result == template:
                break
            template = result
        
        return template
    
    def _resolve_dict_vars(self, d: Dict[str, Any]) -> Dict[str, Any]:
        """Iteratively resolve {var} references within dict values."""
        for _ in range(5):
            changed = False
            for k, v in d.items():
                if isinstance(v, str) and '{' in v:
                    new_v = self._resolve_str(v, d)
                    if new_v != v:
                        d[k] = new_v
                        changed = True
            if not changed:
                break
        return d
    
    def list_available_queries(self) -> List[Dict[str, str]]:
        """List all available recipe+variant combinations with their output tag names."""
        result = []
        for rname, recipe in self.recipe_lib._recipes.items():
            for vname, variant in recipe.get('variants', {}).items():
                result.append({
                    'recipe': rname,
                    'variant': vname,
                    'output_tag': variant.get('output_tag_name', ''),
                    'tag_name': variant.get('tag_name', ''),
                })
        return result


# Convenience function for use by concept_router
def assemble_sql(recipe_name: str, variant_name: str, 
                 extra_params: Dict[str, Any] = None) -> str:
    """One-shot assembly: create assembler and produce SQL."""
    assembler = BlockAssembler()
    return assembler.assemble(recipe_name, variant_name, extra_params)


# ── 全局单例：避免每次请求重载全部 block/recipe YAML ──
_assembler_instance = None

def get_block_assembler() -> BlockAssembler:
    global _assembler_instance
    if _assembler_instance is None:
        _assembler_instance = BlockAssembler()
    return _assembler_instance


if __name__ == "__main__":
    # Test: assemble agent_cross_conflict
    assembler = BlockAssembler()
    
    print("Available queries:")
    for q in assembler.list_available_queries():
        print(f"  {q['recipe']} / {q['variant']} -> {q['output_tag']} (tag: {q['tag_name']})")
    
    print("\n" + "="*80)
    print("Assembling: conflict_pipeline / vehicle")
    sql = assembler.assemble('conflict_pipeline', 'vehicle')
    print(sql[:2000])
    print(f"\n... total {len(sql)} chars")
