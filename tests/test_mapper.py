
from postmodel import Postmodel, models
import pytest
from postmodel.exceptions import IntegrityError
import asyncio

class Foo(models.Model):
    foo_id = models.IntField(pk=True)
    name = models.CharField(max_length=255, index=True)
    tag = models.CharField(max_length=128)
    memo = models.TextField()
    class Meta:
        table = "foo_mapper"

@pytest.mark.asyncio
async def test_init_1():
    await Postmodel.init('postgres://postgres@localhost:54320/test_db', modules=[__name__])
    assert len(Postmodel._databases) == 1
    assert Postmodel._inited == True
    await Postmodel.generate_schemas()
    await Postmodel.close()

@pytest.mark.asyncio
async def test_init_2():
    await Postmodel.init('postgres://postgres@localhost:54320/test_db', modules=[__name__])
    assert len(Postmodel._databases) == 1
    assert Postmodel._inited == True
    await Postmodel.generate_schemas()
    await Postmodel.close()

@pytest.mark.asyncio
async def test_mapper_1():
    await Postmodel.init('postgres://postgres@localhost:54320/test_db', modules=[__name__])
    assert len(Postmodel._databases) == 1
    assert Postmodel._inited == True
    await Postmodel.generate_schemas()
    mapper = Postmodel.get_mapper(Foo)
    await mapper.delete_table()
    foo = await Foo.create(foo_id=1, name="hello", tag="hi", memo="a long text memo.")
    foo = await Foo.create(foo_id=2, name="hello", tag="hi", memo="a long text memo.")
    with pytest.raises(IntegrityError):
        await Foo.create(foo_id=2, name="hello", tag="hi", memo="a long text memo.")

    foo2 = await Foo.get(foo_id = 1)
    print("foo2>>", foo2)
    #await asyncio.sleep(6)
    ret = await foo.delete()
    print("delete ret>>", ret)
    await Postmodel.close()
