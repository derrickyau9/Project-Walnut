"""Application readiness is separate from showing a diagnostics-capable window."""
import json,os,subprocess

def probe_imager():
    from image_processing import worker_path,PROCESSOR_VERSION
    path=worker_path()
    if path is None:return {'ready':False,'reason':'matching native image worker is unavailable'}
    try:
        result=subprocess.run([str(path),'--capabilities'],capture_output=True,timeout=10,check=True,
                              creationflags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0)
        if len(result.stdout)>16384:raise ValueError('worker capabilities exceeded bound')
        value=json.loads(result.stdout)
        if not isinstance(value,dict) or value.get('format')!='daos.image-processing-capabilities.v1' or value.get('processor')!=PROCESSOR_VERSION or value.get('opencv')!='4.12.0':
            raise ValueError('native image worker identity mismatch')
        return {'ready':True,'processor':value['processor'],'opencv':value['opencv']}
    except (OSError,subprocess.SubprocessError,ValueError) as error:return {'ready':False,'reason':str(error)}
