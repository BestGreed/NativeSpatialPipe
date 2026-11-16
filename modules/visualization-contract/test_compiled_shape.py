import copy
import random
import unittest
from contract import SCHEMA, _compile_shape, _validate_shape_interpreted, validate_shape
from fixtures import examples


def paths(value, prefix=()):
    yield prefix
    if isinstance(value,dict):
        for key,item in value.items():yield from paths(item,prefix+(key,))
    elif isinstance(value,list):
        for index,item in enumerate(value):yield from paths(item,prefix+(index,))


class CompiledShapeTests(unittest.TestCase):
    def test_acceptance_matches_interpreter_on_fixtures_and_mutations(self):
        fast=_compile_shape(SCHEMA)
        fixtures=list(examples().values())
        rng=random.Random(240)
        cases=list(fixtures)
        replacements=[None,True,False,0,-1,2**54,0.5,float('nan'),float('inf'),'',[],{},'unexpected']
        for _ in range(700):
            value=copy.deepcopy(rng.choice(fixtures))
            path=rng.choice(list(paths(value)))
            replacement=copy.deepcopy(rng.choice(replacements))
            if path:
                target=value
                for key in path[:-1]:target=target[key]
                if isinstance(target,dict) and rng.random()<.3:del target[path[-1]]
                else:target[path[-1]]=replacement
            else:value=replacement
            cases.append(value)
        for value in cases:
            results=[]
            for check in (fast,lambda item:_validate_shape_interpreted(item,SCHEMA)):
                try:check(value);results.append(True)
                except ValueError:results.append(False)
            self.assertEqual(results[0],results[1])

    def test_failure_diagnostics_and_custom_schemas_preserved(self):
        for value in ({},None,{'forbiddenPCM':[0.]}):
            with self.assertRaises(ValueError) as original:_validate_shape_interpreted(value,SCHEMA,'input')
            with self.assertRaises(ValueError) as fast:validate_shape(value,SCHEMA,'input')
            self.assertEqual(str(original.exception),str(fast.exception))
        schema={'type':'integer','minimum':0}
        validate_shape(1,schema)
        schema['minimum']=2
        with self.assertRaises(ValueError):validate_shape(1,schema)


if __name__=='__main__':unittest.main()
