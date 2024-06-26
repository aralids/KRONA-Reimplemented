from flask import render_template, request, jsonify
from app import app
import taxopy
import csv
import json
import copy
from werkzeug.utils import secure_filename

taxdb = None
taxdb = taxopy.TaxDb()

def flatten(l):
    return [item for sublist in l for item in sublist]

def get_count(key):
    return key["count"]

# previously @app.route('/index')
@app.route('/')
def index():
    return render_template('index.html')

@app.route('/load_tsv_data', methods=["POST", "GET"])
def load_tsv_data():
    taxDict = {"root": {"taxID": "1", "unassignedCount": 0, "totalCount": 0, "lineageNames": [["root", "root"]], "rank": "root", "names": [["root", -1]], "geneNames": [], "eValues": [], "fastaHeaders": [], "descendants": []}}
    taxID_sum = 84
    tempDict = {"1": "root"}
    allEvals = []
    if request.method == 'POST':
        f = request.files['file'].read()
        whole_file = (f.decode("utf-8")[:-1]).split("\n")
        first_line = whole_file[0]
        tsv_file = whole_file[1:]
        e_value_included = "bh_evalue" in first_line
        fasta_header_included = "fasta_header" in first_line
        for line in tsv_file:
            taxDict, tempDict, taxID_sum, allEvals = read_line(line, taxDict, tempDict, taxID_sum, allEvals, e_value_included, fasta_header_included)
            median = None
        if e_value_included:
            median = sorted(allEvals)[len(allEvals) // 2]

    for taxon in taxDict.keys():
        subtaxa_counts = [taxDict[other_taxon]["unassignedCount"] for other_taxon in taxDict.keys() if taxon in flatten(taxDict[other_taxon]["lineageNames"])]
        taxDict[taxon]["totalCount"] = sum(subtaxa_counts)
    offset = sum_to_2dig(str(taxID_sum))
    rankPatternFull = ["root", "superkingdom", "kingdom", "subkingdom", "superphylum", "phylum", "subphylum", "superclass", "class", "subclass", "superorder", "order", "suborder", "superfamily", "family", "subfamily", "supergenus", "genus", "subgenus", "superspecies", "species"]

    # Get the names of all taxa with reducible lineages in a list.
    # For the taxa in taxaWithReducibleLineages, reduce their lineages 
    # in allTaxaReduced by removing each rank which is not in the full rank pattern.
    # Mark all taxa in allTaxaReduced which have to be deleted (the taxon is most likely a clade) by putting it in the reducibleTaxa list.
    # Add "root" as first item in the lineage of every taxon in allTaxaReduced.
    allTaxaReduced = copy.deepcopy(taxDict)
    reducibleTaxa = []
    for key,value in taxDict.items():
        lineage = value["lineageNames"]
        rank = value["rank"]
        reducedLineage = copy.deepcopy(lineage)
        for i in reversed(range(0, len(lineage))):
            if not (lineage[i][0] in rankPatternFull):
                del reducedLineage[i]
        if (key != "root"):
            reducedLineage = [allTaxaReduced["root"]["lineageNames"][0]] + reducedLineage
        allTaxaReduced[key]["lineageNames"] = reducedLineage
        if (len(reducedLineage) == 0) or rank != reducedLineage[len(reducedLineage)-1][0]:
            reducibleTaxa.append(key)

    # Delete reducible taxa from allTaxaReduced and add new ones, their lastPredecessor, if necessary.
    # (else) If the last predecessor is not in allTaxaReduced (doesn't have its own counts in the raw file), add it to newlyAdded and allTaxaReduced.
    # (if) If the last predecessor is in allTaxaReduced and was added in a previous iteration of this loop (doesn't have its own counts in the raw file).
    # (elif) If the last predecessor is in allTaxaReduced but did exist before this loop (has its own counts in the raw file).
    # Delete reducible taxon.
    newlyAdded = []
    for taxName in reducibleTaxa:
        unassignedCount = taxDict[taxName]["unassignedCount"]
        lineage = allTaxaReduced[taxName]["lineageNames"]
        g_names = allTaxaReduced[taxName]["geneNames"]
        lastPredecessor = lineage[-1][1]

        if lastPredecessor in allTaxaReduced.keys():
            if lastPredecessor == "root" and (not lastPredecessor in newlyAdded):
                allTaxaReduced[lastPredecessor]["totalCount"] += unassignedCount
            allTaxaReduced[lastPredecessor]["unassignedCount"] += unassignedCount
            allTaxaReduced[lastPredecessor]["names"].append([taxName, allTaxaReduced[lastPredecessor]["names"][-1][1] + unassignedCount])
            allTaxaReduced[lastPredecessor]["geneNames"] += g_names
            if e_value_included:
                allTaxaReduced[lastPredecessor]["eValues"] += allTaxaReduced[taxName]["eValues"]
            if fasta_header_included:
                allTaxaReduced[lastPredecessor]["fastaHeaders"] += allTaxaReduced[taxName]["fastaHeaders"]
            if (not lastPredecessor in newlyAdded) and (not "deletedDescendants" in allTaxaReduced[lastPredecessor]):
                allTaxaReduced[lastPredecessor]["deletedDescendants"] = [taxName]
            else:
                allTaxaReduced[lastPredecessor]["deletedDescendants"].append(taxName)

        else:
            newlyAdded.append(lastPredecessor)
            newRank = lineage[-1][0]
            allTaxaReduced[lastPredecessor] = {}
            allTaxaReduced[lastPredecessor]["lineageNames"] = lineage
            allTaxaReduced[lastPredecessor]["rank"] = newRank
            allTaxaReduced[lastPredecessor]["totalCount"] = 0
            allTaxaReduced[lastPredecessor]["unassignedCount"] = unassignedCount
            allTaxaReduced[lastPredecessor]["deletedDescendants"] = [taxName]
            allTaxaReduced[lastPredecessor]["names"] = [[taxName, unassignedCount-1]]
            allTaxaReduced[lastPredecessor]["geneNames"] = g_names
            if e_value_included:
                allTaxaReduced[lastPredecessor]["eValues"] = allTaxaReduced[taxName]["eValues"]
            if fasta_header_included:
                allTaxaReduced[lastPredecessor]["fastaHeaders"] = allTaxaReduced[taxName]["fastaHeaders"]
            allTaxaReduced[lastPredecessor]["descendants"] = []
        
        del allTaxaReduced[taxName]

    # Last predecessors have been added. Now add all non-last predecessors that don't have their own unassigned counts as items in allTaxaReduced and newlyAdded.
    for taxName in list(allTaxaReduced.keys()):
        unassignedCount = allTaxaReduced[taxName]["unassignedCount"]
        lineage = allTaxaReduced[taxName]["lineageNames"]
        allTaxaReducedKeys = list(allTaxaReduced.keys())
        for predecessor in lineage:
            if (not (predecessor[1] in allTaxaReducedKeys and allTaxaReduced[predecessor[1]]["rank"] == predecessor[0])) and (not predecessor[1] + " " + predecessor[0] in allTaxaReducedKeys):
                newName = predecessor[1] + " " + predecessor[0]
                newlyAdded.append(newName)
                allTaxaReduced[newName] = {}
                allTaxaReduced[newName]["rank"] = predecessor[0]
                allTaxaReduced[newName]["lineageNames"] = lineage[:lineage.index(predecessor)+1]
                allTaxaReduced[newName]["totalCount"] = 0
                allTaxaReduced[newName]["unassignedCount"] = 0
                allTaxaReduced[newName]["names"] = []
                allTaxaReduced[newName]["geneNames"] = []
                if e_value_included:
                    allTaxaReduced[newName]["eValues"] = []
                if fasta_header_included:
                    allTaxaReduced[newName]["fastaHeaders"] = []
                allTaxaReduced[newName]["descendants"] = []

    # For every taxon in allTaxaReduced, if a newlyAdded taxon shows up as a predecessor in its lineage, change the predecessor's name to taxon + rank (as opposed to just taxon).
    for taxName in list(allTaxaReduced.keys()):
        lineage = allTaxaReduced[taxName]["lineageNames"]
        for predecessor in lineage:
            if predecessor[1] + " " + predecessor[0] in newlyAdded:
                predecessor[1] = predecessor[1] + " " + predecessor[0]
    
    # For each newlyAdded taxon, add the unassigned counts of the taxa in whose lineages it shows up to its total count.
    for taxName in list(allTaxaReduced.keys()):
        unassignedCount = allTaxaReduced[taxName]["unassignedCount"]
        lineage = allTaxaReduced[taxName]["lineageNames"]
        allTaxaReducedKeys = list(allTaxaReduced.keys())
        for predecessor in lineage:
            if predecessor[1] in newlyAdded:
                allTaxaReduced[predecessor[1]]["totalCount"] += unassignedCount

        # If a rank shows up twice (or multiple times) in the lineage of a taxon, remove the first occurrence from the lineage.
        if taxName != "root":
            allTaxaReduced["root"]["totalCount"] += allTaxaReduced[taxName]["unassignedCount"]
        lineage = allTaxaReduced[taxName]["lineageNames"]
        for i in range(0, len(lineage)-1):
            if allTaxaReduced[taxName]["unassignedCount"] > 0:
                allTaxaReduced[lineage[i][1]]["descendants"].append(taxName)
            # Removing now causes issues.
            if lineage[i][0] == lineage[i+1][0]:
                del lineage[i]

    lineagesFull = []
    for taxName in list(filter(lambda item: (allTaxaReduced[item]["unassignedCount"] != 0), list(allTaxaReduced.keys()))):
        lineagesFull.append(list(map(lambda item: item[1] + "_*_" + item[0], allTaxaReduced[taxName]["lineageNames"])))
    lineagesFull.sort()

    lineagesNames = []
    lineagesRanks = []
    for lineage in lineagesFull:
        lineageNames = list(map(lambda item: item.split("_*_")[0], lineage))
        lineageRanks = list(map(lambda item: item.split("_*_")[1], lineage))
        lineagesNames.append(lineageNames)
        lineagesRanks.append(lineageRanks)

    return jsonify({"lineagesNames": lineagesNames, "lineagesRanks": lineagesRanks, "allTaxaReduced": allTaxaReduced, "rankPatternFull": rankPatternFull, "allTaxa": taxDict, "offset": offset, "median": median, "fastaHeaderIncluded": fasta_header_included})

@app.route('/load_fasta_data', methods=["POST", "GET"])
def load_fasta_data():
    f = request.files['fasta-file'].read()
    fasta_file = (f.decode("utf-8")[:-1]).split(">")
    dict = {}
    for seq in fasta_file:
        seq2list = seq.split("\n")
        if len(seq2list) > 1:
            seq_name = seq2list[0]
            seq_body = seq2list[1].replace("*", "")
            dict[seq_name] = seq_body
    return jsonify({"headerSeqObject": dict})
   
def sum_to_2dig(sum_str, start=0, end=2):
    if start == len(sum_str):
        return 0
    elif end-1 == len(sum_str):
        return int(sum_str[start])
    else:
        sum = int(sum_str[start:end]) + sum_to_2dig(sum_str, start+2, end+2)
        if sum > 100:
            return sum_to_2dig(str(sum))
        else:
            return sum 
        
@app.route('/fetchID', methods = ['GET'])
def fetchID():
    print("taxName for fetching ID: ", request.args["taxName"])
    taxid = taxopy.taxid_from_name(request.args["taxName"], taxdb)
    return jsonify({"taxID": taxid[0]})

def read_line(line, taxDict, tempDict, taxID_sum, allEvals, e_value_included, fasta_header_included):
    line2list = line.split("\t")
    line2list = [item.replace("\r","") for item in line2list]
    gene_name = line2list[0]
    taxID = line2list[1]
    if e_value_included:
        e_value = line2list[2]
    if fasta_header_included:
        fasta_header = line2list[3]
    if taxID == "NA" or taxID == "":
        taxID = "1"

    # taxID lineageNames unassignedCount rank totalCount names geneNames descendants [eValues, [fastaHeaders]]

    if not (taxID in tempDict):
        taxID_sum += int(taxID)
        taxon = taxopy.Taxon(int(taxID), taxdb)
        name = taxon.name
        rank = taxon.rank
        lineageNamesList = taxon.rank_name_dictionary
        dictlist = []

        prevRank = None
        for r, n in lineageNamesList.items():
            # Make sure no rank comes up twice in a lineage
            if prevRank == r: 
                continue
            
            dictlist = [[r, n]] + dictlist
            prevRank = r

        if name in taxDict:
            name += " 1"
        taxDict[name] = {"taxID": taxID, "lineageNames": dictlist, "unassignedCount": 1, "rank": rank, "totalCount": 1, "names": [[name, 0]], "geneNames": [gene_name], "descendants": []}
        
        if e_value_included:
            if e_value != "":
                taxDict[name]["eValues"] = [float(e_value)]
                allEvals.append(float(e_value))
            else:
                taxDict[name]["eValues"] = [None]
            
        if fasta_header_included:
            if fasta_header != "":
                taxDict[name]["fastaHeaders"] = [fasta_header]
            else:
                taxDict[name]["fastaHeaders"] = [None]

        if len(dictlist) == 0 or not name in dictlist[-1]:
            taxDict[name]["lineageNames"].append([rank, name])
        tempDict[taxID] = name
    else:
        taxDict[tempDict[taxID]]["totalCount"] += 1
        taxDict[tempDict[taxID]]["unassignedCount"] += 1
        taxDict[tempDict[taxID]]["geneNames"].append(gene_name)
        taxDict[tempDict[taxID]]["names"][0][1] += 1

        if e_value_included:
            if e_value != "":
                taxDict[tempDict[taxID]]["eValues"].append(float(e_value))
                allEvals.append(float(e_value))
            else:
                taxDict[tempDict[taxID]]["eValues"].append(None)

        if fasta_header_included:
            if fasta_header != "":
                taxDict[tempDict[taxID]]["fastaHeaders"].append(fasta_header)
            else:
                taxDict[tempDict[taxID]]["fastaHeaders"].append(None)
    return taxDict, tempDict, taxID_sum, allEvals