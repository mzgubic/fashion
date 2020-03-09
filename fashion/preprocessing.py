import pandas as pd
import numpy as np
import pickle
import time
import sys
import os
sys.path.insert(0, '..')
from fashion import utils


def load_shops(path=utils.loc / 'data' / '20200120_filiali.csv', extra_info=True):

    # load
    df = pd.read_csv(path, low_memory=False)

    # translate the columns
    new_columns = ['StoreKey', 'Franchise', 'StoreType', 'Outlet', 'ZipCode', 'City']
    df.rename(inplace=True, columns=dict(zip(df.columns, new_columns)))

    # filter outlets
    df = df[df.Outlet != 'H']
    df.drop(['Outlet'], axis=1, inplace=True)

    # franchise correct
    df.Franchise = df.Franchise.map({'I':True, ' ':False})

    # StoreType
    df.StoreType = df.StoreType.map({' ':'Store'})

    # add extra information
    if extra_info:
        for kind in ['Unique', 'Total']:
            df['N{}ProductsSold'.format(kind)] = get_nproducts_in_shop(df, kind)

    return df


def get_nproducts_in_shop(shops, kind):

    # load or compute the number of products sold in each store
    fpath = utils.loc / 'data' / '{}_product_counts.pkl'.format(kind)
    print(fpath)
    if os.path.exists(fpath):
        print('Loading {} product counts in stores'.format(kind))
        prod_counts = pickle.load(open(fpath, 'rb'))

    else:
        print('Computing {} product counts in stores'.format(kind))
        # load and concatenate sales
        sales = load_sales(utils.loc / 'data' / '20200120_sales17.csv', shops)
        if kind == 'Unique':
            sales1819 = load_sales(utils.loc / 'data' / '20200120_sales1819.csv', shops)
            sales = pd.concat([sales, sales1819], axis=0)

        gb = sales.groupby(by='StoreKey')
        if kind == 'Unique':
            counts = gb["EAN"].nunique()
        elif kind == 'Total':
            counts = gb["Volume"].sum()

        prod_counts = {sk:counts[sk] for sk in shops.StoreKey if sk in counts}
        pickle.dump(prod_counts, open(fpath, 'wb'))

    # return an array of counts
    res = np.zeros(len(shops), dtype=int)
    for i, sk in enumerate(shops.StoreKey):
        nprod = prod_counts[sk] if sk in prod_counts else 0
        res[i] = nprod

    return res


def load_sales(path=utils.loc / 'data' / '20200120_sales17.csv', shops_df=load_shops(extra_info=False), nrows=None):

    # load
    if nrows:
        df = pd.read_csv(path, nrows=nrows)
    else:
        df = pd.read_csv(path)

    # translate the columns
    new_columns = ['StoreKey', 'ReceiptKey', 'Date', 'Hour', 'EAN', 'Volume', 'NetIncome']
    df.rename(inplace=True, columns=dict(zip(df.columns, new_columns)))

    # remove the ones which belong to warehouses (and delete the column)
    df = df.merge(shops_df[['StoreKey', 'StoreType']], how='left', on='StoreKey')
    df = df[df.StoreType == 'Store']
    df.drop(['StoreType'], axis=1, inplace=True)

    # drop some specific items from sales17
    df = df[df.NetIncome != 20901.0]
    df = df[df.NetIncome != -20901.0]
    df = df[df.NetIncome != 9095.0]
    df = df[df.NetIncome != -9095.0]

    return df


def load_products(path=utils.loc / 'data' / '20200120_barcode.csv'):

    # load the
    df = pd.read_csv(path, low_memory=False)

    # translate the columns
    new_columns = ['EAN', 'ProductID', 'ColorCode', 'ColorDescription',
                   'Size', 'Gender', 'ItemCode', 'ItemDescription',
                   'ProductCategory', 'Season', 'Year', 'ColorID',
                   'SizeID', 'OriginalListedPrice', 'StandardCostOfGoods',
                   'ReceivedItems', 'IntroductionPeriod', 'Lifecycle',
                   'PriceRange', 'FashionContent', 'ItemWeight', 'InnovationContent']
    df.rename(inplace=True, columns=dict(zip(df.columns, new_columns)))

    # remove duplicate values (or unnecessary ones)
    cols_rm=['ColorID']
    df.drop(cols_rm, axis=1, inplace=True)

    # reduce the duplicates in colours
    def colour_transform(s):
        for c in ['-', '/', '.']:
            s = s.replace(c, ' ')
        return s.lower().split()[0]
    cd = 'ColorDescription'
    df[cd] = df[cd].apply(lambda s: colour_transform(s))

    # and keep them to 50 most popular colours
    nkeep = 50
    populars = df[cd].value_counts().index[:nkeep]
    df[cd] = df[cd].apply(lambda s: s if s in populars else 'other')

    # keep only the most common sizes (such that 95% of items retain their size, other sizes will be marked as 'other')
    size = 'Size'
    cs = np.cumsum(df[size].value_counts())
    nkeep = np.sum(cs < 0.95 * cs.iloc[-1])
    populars = df[size].value_counts().index[:nkeep]
    df[size] = df[size].apply(lambda s: s if s in populars else 'other')

    return df


def get_stores_in_sales_data():

    fpath = utils.loc / 'data' / 'cache_sales_stores.csv'
    if os.path.exists(fpath):
        sales_stores = pickle.load(open(fpath, 'rb'))

    else:
        print('{} does not exist yet, computing it now.'.format(fpath))
        sales17 = load_sales(utils.loc / 'data' / '20200120_sales17.csv')
        stores17 = sales17.StoreKey.unique()
        sales1819 = load_sales(utils.loc / 'data' / '20200120_sales1819.csv')
        stores1819 = sales1819.StoreKey.unique()

        sales_stores = set(stores17).union(set(stores1819))
        pickle.dump(sales_stores, open(fpath, 'wb'))

    return sales_stores


@utils.cache
def size_groups():

    prods = load_products()
    pids = prods.ProductID.unique()
    groups = set()
    pid2group = {}
    for i, pid in enumerate(pids):

        group = tuple(s for s in prods[prods.ProductID == pid].Size.unique())
        groups.add(group)
        pid2group[pid] = group

    both = groups, pid2group

    return both


@utils.cache
def size_corrections():

    # load sales data (17 only)
    sales = load_sales()

    # prepare some mappings
    groups, pid2group = size_groups()

    ean2pid = EAN2pid()
    ean2size = EAN2size()

    # compute the dict
    N_total = len(sales)
    N_test = 10000
    dist = {pid:{s:0 for s in pid2group[pid]} for pid in pid2group}

    t0 = time.time()
    for i in range(len(sales)):
        row = sales.iloc[i]
        ean = row['EAN']
        pid = ean2pid[ean]
        size = ean2size[ean]
        dist[pid][size] += 1

        if i == N_test:
            minutes_taken = (time.time()-t0) / 60
            minutes_total = N_total / N_test * minutes_taken
            print('based on loop of {}, full run will take {:.2f}m'.format(N_test, minutes_total))
            continue

    # divide by the total for each product
    sc = {p:{s:0 for s in dist[p]} for p in dist}
    for p in dist:
        total = np.sum([c for c in dist[p].values()])
        if total == 0:
            print('WARNING: no products found for {}, {}'.format(p, dist[p]))
            continue
        corrections = {s:dist[p][s] / total for s in dist[p]}
        sc[p] = corrections

    return sc


@utils.cache
def EAN2size():

    # compute the dict
    prods = load_products()

    e2s = {}
    for i in range(len(prods)):
        row = prods.iloc[i]
        e2s[row['EAN']] = row['Size']

    return e2s


@utils.cache
def EAN2pid():

    # compute the dict
    prods = load_products()

    e2p = {}
    for i in range(len(prods)):
        row = prods.iloc[i]
        e2p[row['EAN']] = row['ProductID']

    return e2p


def main():

    #shops = load_shops()

    o = EAN2pid()
    i = EAN2size()
    s = size_groups()
    sc = size_corrections()


    #import matplotlib.pyplot as plt
    #for var in ['NTotalProductsSold', 'NUniqueProductsSold']:
    #    fig, ax = plt.subplots()
    #    ax.hist(shops[var], bins=100)
    #    ax.set_xlabel(var)
    #    ax.set_ylabel('Number of stores')
    #    plt.savefig(utils.loc / 'figures' / '{}.pdf'.format(var))






if __name__ == '__main__':
    main()
